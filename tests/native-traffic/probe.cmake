# SPDX-License-Identifier: GPL-3.0-only
# Included in the pinned Qt source directory only for the disposable test build.
omim_add_executable(volparossa_traffic_probe
  "${CMAKE_CURRENT_LIST_DIR}/routing_probe.cpp"
  "${CMAKE_CURRENT_SOURCE_DIR}/local_traffic.cpp")
target_link_libraries(volparossa_traffic_probe PRIVATE generator qt_common map location_service)
