// SPDX-License-Identifier: GPL-3.0-only
package org.volparossa.map.dps;

import static org.junit.Assert.*;

import android.Manifest;
import android.app.Activity;
import android.app.ActivityManager;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.location.Location;
import android.os.Bundle;
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
    private static void supply(MwmActivity activity, boolean verticalAccuracy, long wallAgeMs) {
        supply(activity, verticalAccuracy, wallAgeMs, false);
    }

    private static void supply(MwmActivity activity, boolean verticalAccuracy, long wallAgeMs, boolean simulated) {
        LocationHelper helper = MwmApplication.from(activity).getLocationHelper();
        helper.start();
        Location location = new Location("gps");
        location.setLatitude(43.737);
        location.setLongitude(7.427);
        location.setAltitude(32);
        location.setAccuracy(3);
        if (verticalAccuracy) location.setVerticalAccuracyMeters(4);
        location.setElapsedRealtimeNanos(SystemClock.elapsedRealtimeNanos());
        location.setTime(System.currentTimeMillis() - wallAgeMs);
        if (simulated) location.setMock(true); // The installed trial runs on API 35.
        helper.onLocationChanged(location);
        assertSame("Organic Maps did not accept the fixture", location, helper.getSavedLocation());
    }

    private static void observed(String check) {
        Bundle status = new Bundle();
        status.putString("volparossa_checkpoint", check);
        InstrumentationRegistry.getInstrumentation().sendStatus(2, status);
    }

    @Test public void permissionDenied() throws Exception {
        MwmActivity activity = openFromLauncher();
        openPosition(activity);
        observed("position_open");
        onUi(() -> {
            assertEquals(PackageManager.PERMISSION_DENIED, activity.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION));
            MyPositionQuality quality = view(activity);
            assertEquals("", visibleText(quality));
            tap(quality);
            assertEquals(activity.getString(R.string.volparossa_quality_permission), visibleText(quality));
            observed("permission_denied");
            tap(quality);
            assertEquals("", visibleText(quality));
            observed("cleared");
        });
    }

    @Test public void acceptedAndIncompleteFixes() throws Exception {
        MwmActivity activity = openFromLauncher();
        openPosition(activity);
        observed("position_open");
        onUi(() -> {
            assertEquals(PackageManager.PERMISSION_GRANTED, activity.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION));
            MyPositionQuality quality = view(activity);
            supply(activity, true, 0);
            assertEquals("", visibleText(quality));
            tap(quality);
            assertEquals(activity.getString(R.string.volparossa_quality_single, 5.0), visibleText(quality));
            observed("single_fix_after_tap");
            supply(activity, false, 0);
            assertEquals("", visibleText(quality));
            observed("replacement_cleared");
            tap(quality);
            assertEquals(activity.getString(R.string.volparossa_quality_unavailable), visibleText(quality));
            observed("missing_vertical_refused");
            tap(quality);
            supply(activity, true, 6000);
            tap(quality);
            assertEquals(activity.getString(R.string.volparossa_quality_unavailable), visibleText(quality));
            observed("old_time_refused");
        });
    }

    @Test public void simulatedFixProvenance() throws Exception {
        MwmActivity activity = openFromLauncher();
        openPosition(activity);
        observed("position_open");
        onUi(() -> {
            MyPositionQuality quality = view(activity);
            supply(activity, true, 0, true);
            assertEquals("", visibleText(quality));
            tap(quality);
            assertEquals(activity.getString(R.string.volparossa_quality_simulated), visibleText(quality));
            observed("simulated_fix_identified");
            supply(activity, true, 0);
            assertEquals("", visibleText(quality));
            tap(quality);
            assertEquals(activity.getString(R.string.volparossa_quality_single, 5.0), visibleText(quality));
            observed("replacement_assessed_after_tap");
            supply(activity, true, 6000, true);
            tap(quality);
            assertEquals(activity.getString(R.string.volparossa_quality_unavailable), visibleText(quality));
            observed("stale_simulation_refused");

            LocationHelper helper = MwmApplication.from(activity).getLocationHelper();
            Location accepted = helper.getSavedLocation();
            Location inaccurate = new Location(accepted);
            inaccurate.setProvider("network");
            inaccurate.setAccuracy(0);
            helper.onLocationChanged(inaccurate);
            assertSame(accepted, helper.getSavedLocation());
            Location older = new Location(accepted);
            older.setElapsedRealtimeNanos(accepted.getElapsedRealtimeNanos() - 1);
            helper.onLocationChanged(older);
            assertSame(accepted, helper.getSavedLocation());
            observed("provider_filters_preserved");
        });
    }

    @Test public void expiryOfSelectedObservation() throws Exception {
        MwmActivity activity = openFromLauncher();
        openPosition(activity);
        observed("position_open");
        onUi(() -> {
            MyPositionQuality quality = view(activity);
            supply(activity, true, 0);
            LocationHelper helper = MwmApplication.from(activity).getLocationHelper();
            // A fresh copy models a fixed selected observation just for this expiry check.
            Location fixed = new Location(helper.getSavedLocation());
            helper.stop();
            quality.setSource(() -> fixed);
            tap(quality);
            assertEquals(activity.getString(R.string.volparossa_quality_single, 5.0), visibleText(quality));
            observed("selected_fix_shown");
        });
        SystemClock.sleep(5200);
        onUi(() -> {
            MyPositionQuality quality = view(activity);
            assertEquals("", visibleText(quality));
            observed("selected_fix_cleared");
            tap(quality);
            assertEquals(activity.getString(R.string.volparossa_quality_unavailable), visibleText(quality));
            observed("expired_fix_refused");
        });
    }

    @Test public void foregroundAndSelectionRevocation() throws Exception {
        MwmActivity activity = openFromLauncher();
        openPosition(activity);
        observed("position_open");
        onUi(() -> {
            MyPositionQuality quality = view(activity);
            supply(activity, true, 0);
            tap(quality);
            assertEquals(activity.getString(R.string.volparossa_quality_single, 5.0), visibleText(quality));
            observed("single_fix_after_tap");
            assertTrue(activity.moveTaskToBack(true));
        });
        await("The map did not enter the background", () -> {
            Stage stage = ActivityLifecycleMonitorRegistry.getInstance().getLifecycleStageOf(activity);
            return stage == Stage.STOPPED || stage == Stage.DESTROYED;
        });
        observed("map_backgrounded");
        onUi(() -> {
            MyPositionQuality previous = view(activity);
            if (previous != null) assertEquals("", visibleText(previous));
            ActivityManager manager = activity.getSystemService(ActivityManager.class);
            for (ActivityManager.AppTask task : manager.getAppTasks()) {
                if (task.getTaskInfo().taskId == activity.getTaskId()) {
                    // Restore the existing Android task as Recents does. A new
                    // launcher intent can create a separate, transient SplashActivity.
                    task.moveToFront();
                    return;
                }
            }
            fail("The background map task was not available");
        });
        observed("existing_task_requested");
        await("The original map task did not resume", () -> {
            MwmActivity current = resumedMap();
            return current != null && current.getTaskId() == activity.getTaskId();
        });
        MwmActivity resumed = ui(MyPositionQualityTest::resumedMap);
        assertNotNull(resumed);
        if (!ui(() -> view(resumed) != null && view(resumed).isShown() && view(resumed).hasWindowFocus()))
            openPosition(resumed);
        onUi(() -> {
            MyPositionQuality quality = view(resumed);
            assertEquals("", visibleText(quality));
            observed("resumed_without_assessment");
            resumed.onPlacePageActivated(MapObject.createMapObject(MapObject.API_POINT, "Place", "", 43.737, 7.427));
            assertFalse(quality.isShown());
            observed("other_place_hidden");
        });
    }
}
