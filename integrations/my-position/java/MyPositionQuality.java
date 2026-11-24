// SPDX-License-Identifier: GPL-3.0-only
package org.volparossa.map.dps;

import android.Manifest;
import android.content.Context;
import android.content.pm.PackageManager;
import android.graphics.Canvas;
import android.location.Location;
import android.os.Build;
import android.os.SystemClock;
import android.util.AttributeSet;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

import androidx.core.content.ContextCompat;
import app.organicmaps.R;

/** One user gesture evaluates one already accepted fix. The view never retains a Location. */
public final class MyPositionQuality extends LinearLayout {
    public interface Source { Location current(); }

    private static final int MAX_AGE_MS = 5000;
    private final Button button;
    private final TextView summary;
    private Source source;
    private long expiresAt;
    private boolean usesPrecisePermission;
    private final Runnable expiry = this::clear;

    public MyPositionQuality(Context context, AttributeSet attributes) {
        super(context, attributes);
        setOrientation(VERTICAL);
        button = new Button(context);
        button.setText(R.string.volparossa_check_position);
        button.setOnClickListener(v -> {
            if (expiresAt != 0) clear();
            else checkOnce();
        });
        summary = new TextView(context);
        summary.setSaveEnabled(false);
        summary.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
        addView(button);
        addView(summary);
        setVisibility(GONE);
        clear();
    }

    public void setSource(Source selected) {
        clear();
        source = selected;
        setVisibility(selected == null ? GONE : VISIBLE);
    }

    public void clear() {
        if (expiresAt == 0 && summary.getVisibility() == GONE) return;
        expiresAt = 0;
        usesPrecisePermission = false;
        removeCallbacks(expiry);
        summary.setText("");
        summary.setVisibility(GONE);
        button.setText(R.string.volparossa_check_position);
    }

    private boolean permitted() {
        return ContextCompat.checkSelfPermission(getContext(), Manifest.permission.ACCESS_FINE_LOCATION)
            == PackageManager.PERMISSION_GRANTED;
    }

    private void checkOnce() {
        if (!isShown() || !hasWindowFocus() || source == null) return;
        long now = SystemClock.elapsedRealtime();
        long remaining = MAX_AGE_MS;
        String message = getResources().getString(R.string.volparossa_quality_unavailable);
        boolean granted = permitted();
        if (!granted) {
            message = getResources().getString(R.string.volparossa_quality_permission);
        } else if (Build.VERSION.SDK_INT >= 26) {
            Location fix = source.current();
            if (fix != null && fix.hasAltitude() && fix.hasAccuracy() && fix.hasVerticalAccuracy()
                    && fix.getAccuracy() > 0 && fix.getVerticalAccuracyMeters() > 0) {
                // Android's getAltitude is relative to the WGS84 ellipsoid. Do not substitute MSL.
                long fixNanos = fix.getElapsedRealtimeNanos();
                long ageNanos = SystemClock.elapsedRealtimeNanos() - fixNanos;
                long unixMs = System.currentTimeMillis();
                long observedMs = fix.getTime();
                long wallAge = unixMs - observedMs;
                if (fixNanos > 0 && ageNanos >= 0 && ageNanos < MAX_AGE_MS * 1_000_000L
                        && observedMs > 0 && wallAge >= 0 && wallAge < MAX_AGE_MS) {
                    double scale = Math.hypot(fix.getAccuracy(), fix.getVerticalAccuracyMeters());
                    if (Native.evaluate(fix, scale, unixMs)) {
                        message = getResources().getString(R.string.volparossa_quality_single, scale);
                        long ageMs = (ageNanos + 999_999) / 1_000_000;
                        remaining = MAX_AGE_MS - Math.max(ageMs, wallAge);
                    }
                }
            }
        }
        long deadline = now + remaining;
        if (SystemClock.elapsedRealtime() >= deadline || !isShown() || !hasWindowFocus()
                || (granted && !permitted())) return;
        usesPrecisePermission = granted;
        summary.setText(message);
        summary.setVisibility(VISIBLE);
        button.setText(R.string.volparossa_clear_position);
        expiresAt = deadline;
        postDelayed(expiry, deadline - SystemClock.elapsedRealtime());
    }

    @Override protected void dispatchDraw(Canvas canvas) {
        if (expiresAt != 0 && (SystemClock.elapsedRealtime() >= expiresAt
                || (usesPrecisePermission && !permitted()))) clear();
        super.dispatchDraw(canvas);
    }

    @Override public void onWindowFocusChanged(boolean focus) {
        super.onWindowFocusChanged(focus);
        if (!focus && button != null) clear();
    }

    @Override protected void onDetachedFromWindow() {
        clear();
        super.onDetachedFromWindow();
    }

    private static final class Native {
        private static final boolean READY = load();
        private static boolean load() {
            try { System.loadLibrary("volparossa_map_quality"); return true; }
            catch (LinkageError | SecurityException unavailable) { return false; }
        }
        static boolean evaluate(Location fix, double scale, long now) {
            if (!READY || !Double.isFinite(scale)) return false;
            try {
                return assess(fix.getLatitude(), fix.getLongitude(), fix.getAltitude(), scale, fix.getTime(), now);
            } catch (LinkageError unavailable) { return false; }
        }
        private static native boolean assess(double latitude, double longitude, double height,
                                             double comparisonScale, long observedMs, long nowMs);
    }
}
