/* SPDX-License-Identifier: GPL-3.0-only */
#include <jni.h>
#include <stdint.h>
#include "volparossa_dps.h"

/* No pinning Java memory or forwarding Java/native pointers to Rust. */
static int exception_closed(JNIEnv *env) {
  if ((*env)->ExceptionCheck(env)) {
    (*env)->ExceptionClear(env);
    return 1;
  }
  return 0;
}

JNIEXPORT jint JNICALL
Java_org_volparossa_map_dps_NativeQuality_nativeAssess(
    JNIEnv *env, jclass klass, jbyteArray request, jlong now, jintArray output) {
  (void)klass;
  if (request == NULL || output == NULL || now <= 0)
    return VOLPAROSSA_DPS_ARGUMENT;
  jsize size = (*env)->GetArrayLength(env, request);
  if (exception_closed(env)) return VOLPAROSSA_DPS_INTERNAL;
  jsize output_size = (*env)->GetArrayLength(env, output);
  if (exception_closed(env)) return VOLPAROSSA_DPS_INTERNAL;
  if (output_size != 9) return VOLPAROSSA_DPS_ARGUMENT;
  if (size < 1 || (size_t)size > VOLPAROSSA_DPS_MAX_INPUT_BYTES)
    return VOLPAROSSA_DPS_BOUND;
  uint8_t copy[VOLPAROSSA_DPS_MAX_INPUT_BYTES] = {0};
  (*env)->GetByteArrayRegion(env, request, 0, size, (jbyte *)copy);
  DpsQualityV1 quality = {0};
  int result = exception_closed(env) ? VOLPAROSSA_DPS_INTERNAL
      : volparossa_dps_assess_v1(copy, (size_t)size, (uint64_t)now,
                               &quality, sizeof(quality));
  /* Volatile erasure covers the owned request copy, including on closed errors. */
  volatile uint8_t *erase = copy;
  for (jsize i = 0; i < size; ++i) erase[i] = 0;
  if (result != VOLPAROSSA_DPS_OK)
    return result >= VOLPAROSSA_DPS_ARGUMENT && result <= VOLPAROSSA_DPS_INTERNAL
        ? result : VOLPAROSSA_DPS_INTERNAL;
  const jint fields[9] = {(jint)quality.abi_version, (jint)quality.dps_version,
      (jint)quality.status, (jint)quality.frame_kind, (jint)quality.observation_count,
      (jint)quality.eligible_count, (jint)quality.excluded_count,
      (jint)quality.conflict_count, (jint)quality.limitations_mask};
  (*env)->SetIntArrayRegion(env, output, 0, 9, fields);
  return exception_closed(env) ? VOLPAROSSA_DPS_INTERNAL : VOLPAROSSA_DPS_OK;
}
