// SPDX-License-Identifier: GPL-3.0-only
package org.volparossa.map.dps;

import static org.junit.Assert.*;

import android.Manifest;
import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.location.Location;
import android.os.SystemClock;
import android.widget.Button;
import android.widget.TextView;
import androidx.test.platform.app.InstrumentationRegistry;
import androidx.test.runner.lifecycle.ActivityLifecycleMonitorRegistry;
import androidx.test.runner.lifecycle.Stage;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import app.organicmaps.MwmActivity;
import app.organicmaps.MwmApplication;
import app.organicmaps.R;
import app.organicmaps.sdk.Map;
import app.organicmaps.sdk.bookmarks.data.MapObject;
import app.organicmaps.sdk.location.LocationHelper;
import java.util.concurrent.Callable;
import java.util.concurrent.FutureTask;
import java.util.function.BooleanSupplier;
import org.junit.Test;
import org.junit.runner.RunWith;

@RunWith(AndroidJUnit4.class)
public final class MyPositionQualityTest {
    private static MyPositionQuality view(MwmActivity activity) {
        return activity.findViewById(R.id.volparossa_position_quality);
    }

    private static <T> T ui(Callable<T> action) throws Exception {
        FutureTask<T> task = new FutureTask<>(action);
        InstrumentationRegistry.getInstrumentation().runOnMainSync(task);
        return task.get();
    }

    private static void onUi(Runnable action) throws Exception {
        ui(() -> { action.run(); return null; });
    }

    private static void await(String message, BooleanSupplier predicate) throws Exception {
        long deadline = SystemClock.elapsedRealtime() + 20000;
        do {
            if (ui(predicate::getAsBoolean)) return;
            SystemClock.sleep(100);
        } while (SystemClock.elapsedRealtime() < deadline);
        fail(message);
    }

    private static MwmActivity resumedMap() {
        for (Activity candidate : ActivityLifecycleMonitorRegistry.getInstance().getActivitiesInStage(Stage.RESUMED)) {
            if (candidate instanceof MwmActivity activity && activity.hasWindowFocus() && Map.isEngineCreated())
                return activity;
        }
        return null;
    }

    private static MwmActivity openFromLauncher() throws Exception {
        Context app = InstrumentationRegistry.getInstrumentation().getTargetContext();
        Intent launch = app.getPackageManager().getLaunchIntentForPackage(app.getPackageName());
        assertNotNull(launch);
        app.startActivity(launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        await("The normal launcher did not open the initialized map", () -> resumedMap() != null);
        return ui(MyPositionQualityTest::resumedMap);
    }

    private static void openPosition(MwmActivity activity) throws Exception {
        onUi(() -> activity.onPlacePageActivated(
            MapObject.createMapObject(MapObject.MY_POSITION, "", "", 43.737, 7.427)));
        await("The My Position panel did not mount in the foreground", () -> {
            MyPositionQuality quality = view(activity);
            return quality != null && quality.isShown() && quality.hasWindowFocus();
        });
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

    @Test public void permissionDenied() throws Exception {
        MwmActivity activity = openFromLauncher();
        openPosition(activity);
        onUi(() -> {
            assertEquals(PackageManager.PERMISSION_DENIED, activity.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION));
            MyPositionQuality quality = view(activity);
            assertEquals("", visibleText(quality));
            tap(quality);
            assertEquals(activity.getString(R.string.volparossa_quality_permission), visibleText(quality));
            tap(quality);
            assertEquals("", visibleText(quality));
        });
    }

    @Test public void oneFixClearExpiryAndSelection() throws Exception {
        MwmActivity activity = openFromLauncher();
        openPosition(activity);
        onUi(() -> {
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
        onUi(() -> {
            MyPositionQuality quality = view(activity);
            assertEquals("", visibleText(quality));
            tap(quality);
            assertEquals(activity.getString(R.string.volparossa_quality_unavailable), visibleText(quality));
            tap(quality);
            supply(activity, true);
            tap(quality);
            assertEquals(activity.getString(R.string.volparossa_quality_single, 5.0), visibleText(quality));
            assertTrue(activity.moveTaskToBack(true));
        });
        await("The map remained in the foreground", () -> resumedMap() == null);
        MwmActivity resumed = openFromLauncher();
        openPosition(resumed);
        onUi(() -> {
            MyPositionQuality quality = view(resumed);
            assertEquals("", visibleText(quality));
            resumed.onPlacePageActivated(MapObject.createMapObject(MapObject.API_POINT, "Place", "", 43.737, 7.427));
            assertFalse(quality.isShown());
        });
    }
}
