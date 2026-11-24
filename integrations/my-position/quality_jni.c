/* SPDX-License-Identifier: GPL-3.0-only */
#include <jni.h>
#include "volparossa_dps.h"

JNIEXPORT jboolean JNICALL
Java_org_volparossa_map_dps_MyPositionQuality_00024Native_assess(
    JNIEnv *env, jclass klass, jdouble latitude, jdouble longitude, jdouble height,
    jdouble scale, jlong observed_ms, jlong now_ms) {
  (void)env;
  (void)klass;
  if (observed_ms <= 0 || now_ms <= 0) return JNI_FALSE;
  const DpsGeodeticClaimV1 claim = {
    .height_reference = VOLPAROSSA_DPS_WGS84_ELLIPSOID, .reserved = 0,
    .latitude_degrees = latitude, .longitude_degrees = longitude, .height_metres = height,
    .comparison_radius_metres = scale, .observed_at_unix_ms = (uint64_t)observed_ms
  };
  DpsQualityV1 quality = {0};
  const int code = volparossa_dps_assess_geodetic_v1(claim, (uint64_t)now_ms, &quality, sizeof(quality));
  return code == VOLPAROSSA_DPS_OK && quality.abi_version == 1 && quality.dps_version == 1
      && quality.status == 1 && quality.frame_kind == 1 && quality.observation_count == 1
      && quality.eligible_count == 1 && quality.excluded_count == 0 && quality.conflict_count == 0
      && quality.limitations_mask == 0x7f ? JNI_TRUE : JNI_FALSE;
}
