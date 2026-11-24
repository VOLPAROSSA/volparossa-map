// SPDX-License-Identifier: GPL-3.0-only
package org.volparossa.map.dps;

import static org.junit.Assert.*;

import android.Manifest;
import android.content.pm.PackageManager;
import android.location.Location;
import android.os.SystemClock;
import android.widget.Button;
import android.widget.TextView;
import androidx.lifecycle.Lifecycle;
import androidx.test.core.app.ActivityScenario;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import app.organicmaps.MwmActivity;
import app.organicmaps.MwmApplication;
import app.organicmaps.R;
import app.organicmaps.sdk.bookmarks.data.MapObject;
import app.organicmaps.sdk.location.LocationHelper;
import java.util.concurrent.atomic.AtomicBoolean;
import org.junit.Test;
import org.junit.runner.RunWith;

@RunWith(AndroidJUnit4.class)
public final class MyPositionQualityTest {
    private static MyPositionQuality view(MwmActivity activity) {
        return activity.findViewById(R.id.volparossa_position_quality);
    }

    private static void openPosition(ActivityScenario<MwmActivity> scenario) {
        scenario.onActivity(activity -> activity.onPlacePageActivated(
            MapObject.createMapObject(MapObject.MY_POSITION, "", "", 43.737, 7.427)));
        long end = SystemClock.elapsedRealtime() + 15000;
        AtomicBoolean mounted = new AtomicBoolean();
        do {
            scenario.onActivity(activity -> {
                MyPositionQuality quality = view(activity);
                mounted.set(quality != null && quality.isShown() && quality.hasWindowFocus());
            });
            if (mounted.get()) return;
            SystemClock.sleep(100);
        } while (SystemClock.elapsedRealtime() < end);
        fail("The My Position panel did not mount in the foreground");
    }

    private static String visibleText(MyPositionQuality view) {
        return ((TextView)view.getChildAt(1)).getText().toString();
    }

    private static void tap(MyPositionQuality view) {
        assertTrue(((Button)view.getChildAt(0)).performClick());
    }

    // Inputs enter the unmodified Organic Maps provider callback on the isolated emulator.
    private static void supply(MwmActivity activity, boolean verticalAccuracy) {
        LocationHelper helper = MwmApplication.from(activity).getLocationHelper();
        helper.start();
        Location location = new Location("gps");
        location.setLatitude(43.737);
        location.setLongitude(7.427);
        location.setAltitude(32);
        location.setAccuracy(3);
        if (verticalAccuracy) location.setVerticalAccuracyMeters(4);
        location.setElapsedRealtimeNanos(SystemClock.elapsedRealtimeNanos());
        location.setTime(System.currentTimeMillis());
        helper.onLocationChanged(location);
        assertSame("Organic Maps did not accept the fixture", location, helper.getSavedLocation());
    }

    @Test public void permissionDenied() {
        try (ActivityScenario<MwmActivity> scenario = ActivityScenario.launch(MwmActivity.class)) {
            openPosition(scenario);
            scenario.onActivity(activity -> {
                assertEquals(PackageManager.PERMISSION_DENIED, activity.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION));
                MyPositionQuality quality = view(activity);
                assertEquals("", visibleText(quality));
                tap(quality);
                assertEquals(activity.getString(R.string.volparossa_quality_permission), visibleText(quality));
                tap(quality);
                assertEquals("", visibleText(quality));
            });
        }
    }

    @Test public void oneFixClearExpiryAndSelection() {
        try (ActivityScenario<MwmActivity> scenario = ActivityScenario.launch(MwmActivity.class)) {
            openPosition(scenario);
            scenario.onActivity(activity -> {
                assertEquals(PackageManager.PERMISSION_GRANTED, activity.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION));
                MyPositionQuality quality = view(activity);
                supply(activity, true);
                assertEquals("", visibleText(quality));
                tap(quality);
                assertEquals(activity.getString(R.string.volparossa_quality_single, 5.0), visibleText(quality));
                supply(activity, false);
                assertEquals("", visibleText(quality));
                tap(quality);
                assertEquals(activity.getString(R.string.volparossa_quality_unavailable), visibleText(quality));
                tap(quality);
                supply(activity, true);
                tap(quality);
                assertEquals(activity.getString(R.string.volparossa_quality_single, 5.0), visibleText(quality));
                // Keep this fix selected so its time limit alone clears the result.
                MwmApplication.from(activity).getLocationHelper().stop();
            });
            SystemClock.sleep(5200);
            scenario.onActivity(activity -> assertEquals("", visibleText(view(activity))));
            scenario.onActivity(activity -> {
                supply(activity, true);
                tap(view(activity));
            });
            scenario.moveToState(Lifecycle.State.CREATED);
            scenario.moveToState(Lifecycle.State.RESUMED);
            openPosition(scenario);
            scenario.onActivity(activity -> {
                MyPositionQuality quality = view(activity);
                assertEquals("", visibleText(quality));
                activity.onPlacePageActivated(MapObject.createMapObject(MapObject.API_POINT, "Place", "", 43.737, 7.427));
                assertFalse(quality.isShown());
            });
        }
    }
}
