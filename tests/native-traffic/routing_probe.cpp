// SPDX-License-Identifier: GPL-3.0-only
// Disposable Monaco fixture. Use the application's actual Framework, traffic consumer and routing session.
#include "qt/local_traffic.hpp"
#include "map/framework.hpp"
#include "map/routing_mark.hpp"
#include "routing_common/car_model.hpp"
#include "geometry/mercator.hpp"
#include "indexer/feature.hpp"

#include <QApplication>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QSaveFile>
#include <QTimer>

#include <set>

namespace
{
using Keys = std::set<traffic::TrafficInfo::RoadSegmentId>;

void Write(QString const & path, QJsonObject const & value)
{
  QSaveFile file(path);
  if (!file.open(QIODevice::WriteOnly) || file.write(QJsonDocument(value).toJson(QJsonDocument::Compact)) < 0 ||
      !file.commit())
    std::abort();
}

QJsonArray Records(Keys const & keys)
{
  QJsonArray result;
  for (auto const & key : keys)
    result.append(QJsonObject{{"feature_id", qint64(key.GetFid())}, {"segment_index", key.GetIdx()},
                              {"direction", key.GetDir()}, {"speed_group", 0}, {"observation_count", 5}});
  return result;
}

Keys VisibleRoads(Framework const & framework, MwmSet::MwmId const & id)
{
  Keys keys;
  FeaturesLoaderGuard loader(framework.GetDataSource(), id);
  auto const rect = mercator::RectByCenterXYAndSizeInMeters(mercator::FromLatLon(43.73731, 7.42668), 220);
  auto const & car = routing::CarModel::AllLimitsInstance();
  for (uint32_t fid = 0; fid < loader.GetNumFeatures(); ++fid)
  {
    auto ft = loader.GetOriginalFeatureByIndex(fid);
    if (!ft) continue;
    feature::TypesHolder types(*ft);
    if (!car.IsRoad(types)) continue;
    ft->ParseGeometry(FeatureType::BEST_GEOMETRY);
    for (size_t p = 0; p + 1 < ft->GetPointsCount() && p < 32768; ++p)
    {
      if (!rect.IsPointInside(ft->GetPoint(p)) && !rect.IsPointInside(ft->GetPoint(p + 1))) continue;
      keys.emplace(fid, p, 0);
      if (!car.IsOneWay(types)) keys.emplace(fid, p, 1);
    }
  }
  return keys;
}
}

int main(int argc, char ** argv)
{
  if (argc != 5) return 2;
  GetPlatform().SetResourceDir(argv[1]);
  GetPlatform().SetWritableDirForTests(argv[2]);
  QString const dir = argv[3];
  std::string const publisher = argv[4];
  QApplication app(argc, argv);
  FrameworkParams params;
  params.m_useLocalTrafficForRouting = true;
  Framework framework(params);
  framework.SetupMeasurementSystem();
  auto const id = framework.GetDataSource().GetMwmIdByCountryFile(platform::CountryFile("Monaco"));
  if (!id.IsAlive() || id.GetInfo()->GetVersion() != 260928) return 3;
  auto local = qt::StartLocalTraffic(framework, (dir + "/slot.json").toStdString(), publisher, "Monaco");
  if (!local) return 4;
  auto & routing = framework.GetRoutingManager();
  auto & session = routing.RoutingSession();
  Keys roads = VisibleRoads(framework, id);
  if (roads.empty() || roads.size() > 4000) return 5;
  routing.SetRouter(routing::RouterType::Vehicle);
  RouteMarkData start, end;
  start.m_pointType = RouteMarkType::Start;
  start.m_position = mercator::FromLatLon(43.73731, 7.42668);
  end.m_pointType = RouteMarkType::Finish;
  end.m_position = mercator::FromLatLon(43.74483, 7.43229);
  routing.AddRoutePoint(std::move(start), false);
  routing.AddRoutePoint(std::move(end), false);
  int updates = 0;
  routing.SetRouteBuildingListener([&](routing::RouterResultCode code, storage::CountriesSet const &)
  {
    auto const * route = session.GetRoute();
    if (code != routing::RouterResultCode::NoError || !route || !route->IsValid()) return;
    traffic::AllMwmTrafficInfo accepted;
    session.CopyTraffic(accepted);
    QJsonArray geometry;
    int reportedAdvisories = 0;
    for (auto const & leg : route->GetRouteSegments())
    {
      auto const & seg = leg.GetSegment();
      if (seg.IsFakeCreated() || seg.GetMwmId() == routing::kFakeNumMwmId || seg.GetSegmentIdx() >= 32768) continue;
      if (leg.GetTraffic() != traffic::SpeedGroup::Unknown) ++reportedAdvisories;
      geometry.append(QJsonArray{qint64(seg.GetFeatureId()), qint64(seg.GetSegmentIdx()), int(seg.IsForward())});
      if (updates == 0)
        roads.emplace(seg.GetFeatureId(), seg.GetSegmentIdx(), seg.IsForward() ? 0 : 1);
    }
    QJsonObject receipt{{"route_seconds", route->GetTotalTimeSec()}, {"route_metres", route->GetTotalDistanceMeters()},
                        {"geometry", geometry}, {"route_advisories", reportedAdvisories},
                        {"cached_segments", accepted.count(id) ? qint64(accepted.at(id)->size()) : 0}};
    if (!updates) receipt["fixture_roads"] = Records(roads);
    Write(dir + "/route-" + QString::number(++updates) + ".json", receipt);
  });
  QTimer::singleShot(0, [&routing]() { routing.BuildRoute(30); });
  QTimer stop;
  QObject::connect(&stop, &QTimer::timeout, [&]() { if (QFileInfo::exists(dir + "/stop")) app.exit(0); });
  stop.start(200);
  QTimer::singleShot(180000, []() { QCoreApplication::exit(6); });
  return app.exec();
}
