// SPDX-License-Identifier: GPL-3.0-only
package org.volparossa.map.dps;

/** Bare canonical request only; no administrative IPC, sensor or clock acquisition. */
public final class NativeQuality {
    public static final int MAX_INPUT = 16384;
    private NativeQuality() {}
    private static final class Loader {
        static final boolean READY = load();
        private static boolean load() {
            try { System.loadLibrary("volparossa_dps_jni"); return true; }
            catch (LinkageError | SecurityException ignored) { return false; }
        }
    }
    public static final class Result {
        public final int error;
        public final QualitySummary quality;
        private Result(int error, QualitySummary quality) { this.error = error; this.quality = quality; }
        static Result failure(int error) { return new Result(error >= 1 && error <= 5 ? error : 5, null); }
    }
    public static Result assess(byte[] request, long nowUnixMs) {
        if (request == null || nowUnixMs <= 0) return Result.failure(1);
        if (request.length == 0 || request.length > MAX_INPUT) return Result.failure(2);
        if (!Loader.READY) return Result.failure(5);
        int[] fields = new int[9];
        try {
            int status = nativeAssess(request, nowUnixMs, fields);
            if (status != 0) return Result.failure(status);
            return new Result(0, QualitySummary.parse(fields));
        } catch (LinkageError | RuntimeException ignored) { return Result.failure(5); }
    }
    // Package access permits the actual instrumentation boundary tests.
    static native int nativeAssess(byte[] request, long nowUnixMs, int[] output);
}
