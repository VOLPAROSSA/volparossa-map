// SPDX-License-Identifier: GPL-3.0-only
package org.volparossa.map.dps;

import static org.junit.Assert.*;
import android.app.Instrumentation;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.os.SystemClock;
import androidx.test.platform.app.InstrumentationRegistry;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import java.io.InputStream;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.Test;
import org.junit.runner.RunWith;

@RunWith(AndroidJUnit4.class)
public final class QualityInstrumentedTest {
    private final Instrumentation instrumentation = InstrumentationRegistry.getInstrumentation();
    private void ui(Runnable work) { instrumentation.runOnMainSync(work); }
    private byte[] vector(String name) throws Exception {
        try (InputStream input = instrumentation.getContext().getAssets().open(name + ".pb")) {
            byte[] result = input.readAllBytes();
            assertTrue(result.length > 0 && result.length <= NativeQuality.MAX_INPUT);
            return result;
        }
    }
    private void waitUntil(java.util.function.BooleanSupplier test) throws Exception {
        long stop = SystemClock.elapsedRealtime() + 3000;
        while (!test.getAsBoolean() && SystemClock.elapsedRealtime() < stop) {
            instrumentation.waitForIdleSync(); Thread.sleep(10);
        }
        assertTrue("bounded completion", test.getAsBoolean());
    }
    @Test public void actualRustJniGoldensAndClosedErrors() throws Exception {
        String[] names = {"relative_consistent", "relative_conflict", "stale", "empty"};
        int[][] expected = {{1,1,2,2,2,2,0,0,127}, {1,1,3,2,2,2,0,1,127},
                {1,1,0,2,1,0,1,0,127}, {1,1,0,2,0,0,0,0,127}};
        for (int n = 0; n < names.length; ++n) {
            NativeQuality.Result result = NativeQuality.assess(vector(names[n]), 1_000_000);
            assertEquals(0, result.error); assertNotNull(result.quality);
            int[] fields = new int[9];
            assertEquals(0, NativeQuality.nativeAssess(vector(names[n]), 1_000_000, fields));
            assertArrayEquals(expected[n], fields);
        }
        byte[] good = vector("relative_consistent");
        assertEquals(1, NativeQuality.nativeAssess(null, 1, new int[9]));
        assertEquals(1, NativeQuality.nativeAssess(good, 0, new int[9]));
        assertEquals(1, NativeQuality.nativeAssess(good, -1, new int[9]));
        assertEquals(1, NativeQuality.nativeAssess(good, 1, null));
        assertEquals(1, NativeQuality.nativeAssess(good, 1, new int[8]));
        assertEquals(1, NativeQuality.nativeAssess(good, 1, new int[10]));
        assertEquals(2, NativeQuality.nativeAssess(new byte[0], 1, new int[9]));
        assertEquals(2, NativeQuality.nativeAssess(new byte[16385], 1, new int[9]));
        int[] unchanged = {77,77,77,77,77,77,77,77,77};
        assertEquals(3, NativeQuality.nativeAssess(new byte[]{(byte)255}, 1, unchanged));
        assertArrayEquals(new int[]{77,77,77,77,77,77,77,77,77}, unchanged);
        assertEquals(4, NativeQuality.nativeAssess(new byte[]{8,2}, 1, new int[9]));
        byte[] noncanonical = new byte[good.length + 3];
        System.arraycopy(good, 0, noncanonical, 0, good.length);
        noncanonical[good.length] = (byte)0xa0; noncanonical[good.length + 1] = 6;
        noncanonical[good.length + 2] = 1; // Unknown field 100.
        assertEquals(3, NativeQuality.nativeAssess(noncanonical, 1_000_000, new int[9]));
    }
    @Test public void strictSummaryRejectsUnknownOrContradictoryFields() {
        int[] good = {1,1,2,2,2,2,0,0,127};
        assertNotNull(QualitySummary.parse(good));
        int[][] invalid = {new int[8], new int[10], {2,1,2,2,2,2,0,0,127},
                {1,2,2,2,2,2,0,0,127}, {1,1,4,2,2,2,0,0,127},
                {1,1,2,0,2,2,0,0,127}, {1,1,2,2,33,33,0,0,127},
                {1,1,2,2,2,3,0,0,127}, {1,1,2,2,2,2,1,0,127},
                {1,1,2,2,2,2,0,0,126}, {1,1,2,2,2,2,0,0,255},
                {1,1,3,2,2,2,0,2,127}, {1,1,3,2,2,2,0,0,127},
                {1,1,0,2,2,2,0,0,127}, {1,1,1,2,2,2,0,0,127},
                {1,1,2,2,1,1,0,0,127}, {1,1,2,2,2,2,0,-1,127}};
        for (int[] fields : invalid) {
            try { QualitySummary.parse(fields); fail("invalid summary"); }
            catch (IllegalArgumentException expected) { /* Closed structural rejection. */ }
        }
    }
    @Test public void controllerDropsLateRevokedExpiredBackgroundResultsAndJoins() throws Exception {
        byte[] good = vector("relative_consistent");
        AtomicLong clock = new AtomicLong(100);
        AtomicReference<QualitySummary> shown = new AtomicReference<>();
        AtomicInteger calls = new AtomicInteger();
        CountDownLatch entered = new CountDownLatch(1), release = new CountDownLatch(1);
        AtomicReference<QualityController> owner = new AtomicReference<>();
        ui(() -> owner.set(new QualityController(shown::set, clock::get, (input, now) -> {
            calls.incrementAndGet(); entered.countDown();
            // Model a noncancellable native call; interruptions do not fake completion.
            boolean done = false;
            while (!done) try { done = release.await(3, TimeUnit.SECONDS); }
            catch (InterruptedException ignored) { /* Deliberately delayed test worker. */ }
            return NativeQuality.assess(input, now);
        })));
        QualityController c = owner.get();
        try {
            ui(() -> {
                assertFalse(c.submit(good, 1_000_000)); c.foreground();
                assertFalse(c.enable(false, 1000)); assertFalse(c.enable(true, 30001));
                assertTrue(c.enable(true, 1000)); assertTrue(c.submit(good, 1_000_000));
                assertFalse(c.submit(good, 1_000_000));
            });
            assertTrue(entered.await(3, TimeUnit.SECONDS));
            ui(() -> {
                c.revoke(); assertNull(shown.get());
                assertTrue(c.enable(true, 1000));
                assertFalse("same worker remains occupied across generation", c.submit(good, 1_000_000));
                clock.set(2000); c.background(); assertNull(shown.get());
            });
            release.countDown();
            Thread.sleep(100); instrumentation.waitForIdleSync(); assertNull(shown.get());
        } finally { release.countDown(); ui(c::close); }
        assertTrue(c.awaitTermination(3000));
        instrumentation.waitForIdleSync(); assertNull(shown.get()); assertEquals(1, calls.get());
    }
    @Test public void controllerRechecksAuthorityBeforeUiPublicationAndExpiryClears() throws Exception {
        byte[] good = vector("relative_consistent");
        AtomicLong clock = new AtomicLong(0);
        AtomicReference<QualitySummary> shown = new AtomicReference<>();
        AtomicReference<QualityController> owner = new AtomicReference<>();
        CountDownLatch assessed = new CountDownLatch(1);
        ui(() -> owner.set(new QualityController(shown::set, clock::get, (input, now) -> {
            NativeQuality.Result result = NativeQuality.assess(input, now);
            clock.set(1001); assessed.countDown(); return result;
        })));
        QualityController c = owner.get();
        try {
            ui(() -> { c.foreground(); assertTrue(c.enable(true, 1000)); assertTrue(c.submit(good, 1_000_000)); });
            assertTrue(assessed.await(3, TimeUnit.SECONDS));
            instrumentation.waitForIdleSync(); assertNull(shown.get());
        } finally { ui(c::close); }
        assertTrue(c.awaitTermination(3000));

        ui(() -> owner.set(new QualityController(shown::set)));
        final QualityController live = owner.get();
        try {
            ui(() -> { live.foreground(); assertTrue(live.enable(true, 800)); assertTrue(live.submit(good, 1_000_000)); });
            waitUntil(() -> shown.get() != null);
            waitUntil(() -> shown.get() == null);
            ui(() -> { live.background(); live.foreground(); assertFalse(live.submit(good, 1_000_000)); });
        } finally { ui(live::close); }
        assertTrue(live.awaitTermination(3000));
    }
    @Test public void obsoleteGenerationNeverPublishesAfterQueuedCompletion() throws Exception {
        byte[] good = vector("relative_consistent");
        AtomicReference<QualitySummary> shown = new AtomicReference<>();
        CountDownLatch assessed = new CountDownLatch(1);
        AtomicReference<QualityController> owner = new AtomicReference<>();
        ui(() -> owner.set(new QualityController(shown::set, SystemClock::elapsedRealtime, (input, now) -> {
            NativeQuality.Result result = NativeQuality.assess(input, now);
            assessed.countDown(); return result;
        })));
        QualityController c = owner.get();
        try {
            ui(() -> {
                c.foreground(); assertTrue(c.enable(true, 3000)); assertTrue(c.submit(good, 1_000_000));
                try { assertTrue(assessed.await(2, TimeUnit.SECONDS)); }
                catch (InterruptedException e) { throw new AssertionError(e); }
                // Native completion is waiting for this UI runnable to yield.
                c.revoke(); assertTrue(c.enable(true, 3000));
            });
            instrumentation.waitForIdleSync(); assertNull(shown.get());
        } finally { ui(c::close); }
        assertTrue(c.awaitTermination(3000));
    }
    @Test public void publicationWindowExpiryDropsPostedResult() throws Exception {
        byte[] good = vector("relative_consistent");
        AtomicLong clock = new AtomicLong(0);
        AtomicReference<QualitySummary> shown = new AtomicReference<>();
        AtomicInteger publications = new AtomicInteger();
        CountDownLatch posted = new CountDownLatch(1);
        AtomicReference<QualityController> owner = new AtomicReference<>();
        ui(() -> owner.set(new QualityController(q -> {
            shown.set(q); if (q != null) publications.incrementAndGet();
        }, clock::get, NativeQuality::assess, posted::countDown)));
        QualityController c = owner.get();
        try {
            ui(() -> {
                c.foreground(); assertTrue(c.enable(true, 30_000)); assertTrue(c.submit(good, 1_000_000));
                // Deliberately hold the UI only in this test, not while joining.
                // The observer proves the worker already checked fresh authority
                // and enqueued publication before the clock becomes expired.
                try { assertTrue(posted.await(2, TimeUnit.SECONDS)); }
                catch (InterruptedException e) { throw new AssertionError(e); }
                clock.set(30_001);
            });
            instrumentation.waitForIdleSync(); assertNull(shown.get()); assertEquals(0, publications.get());
            ui(() -> assertFalse(c.submit(good, 1_000_000)));
        } finally { ui(c::close); }
        assertTrue(c.awaitTermination(3000));
    }
    @Test public void backgroundDuringFreshInflightWorkDropsLateResultAndResumeStaysDisabled() throws Exception {
        byte[] good = vector("relative_consistent");
        AtomicLong clock = new AtomicLong(100); // Never expires during this test.
        AtomicReference<QualitySummary> shown = new AtomicReference<>();
        AtomicInteger publications = new AtomicInteger();
        CountDownLatch entered = new CountDownLatch(1), release = new CountDownLatch(1), completed = new CountDownLatch(1), settled = new CountDownLatch(1);
        AtomicReference<QualityController> owner = new AtomicReference<>();
        ui(() -> owner.set(new QualityController(q -> {
            shown.set(q); if (q != null) publications.incrementAndGet();
        }, clock::get, (input, now) -> {
            entered.countDown();
            try {
                boolean done = false;
                while (!done) try { done = release.await(3, TimeUnit.SECONDS); }
                catch (InterruptedException ignored) { /* Model a synchronous native call. */ }
                return NativeQuality.assess(input, now);
            } finally { completed.countDown(); }
        }, new QualityController.PublicationObserver() {
            @Override public void posted() { /* Settlement below also covers queued publication. */ }
            @Override public void completed() { settled.countDown(); }
        })));
        QualityController c = owner.get();
        try {
            ui(() -> { c.foreground(); assertTrue(c.enable(true, 30_000)); assertTrue(c.submit(good, 1_000_000)); });
            assertTrue(entered.await(2, TimeUnit.SECONDS));
            ui(() -> { c.background(); assertNull(shown.get()); c.foreground(); });
            release.countDown(); assertTrue(completed.await(2, TimeUnit.SECONDS));
            assertTrue(settled.await(2, TimeUnit.SECONDS));
            instrumentation.waitForIdleSync(); assertNull(shown.get()); assertEquals(0, publications.get());
            ui(() -> assertFalse(c.submit(good, 1_000_000)));
        } finally { release.countDown(); ui(c::close); }
        assertTrue(c.awaitTermination(3000));
    }
    @Test public void actualDebugActivityDisplayRevokeBackgroundAndPermissionBoundary() throws Exception {
        Context context = instrumentation.getTargetContext();
        PackageInfo info = context.getPackageManager().getPackageInfo(context.getPackageName(), PackageManager.GET_PERMISSIONS);
        assertTrue("fixture declares no permissions", info.requestedPermissions == null || info.requestedPermissions.length == 0);
        QualityActivity activity = (QualityActivity)instrumentation.startActivitySync(
                new Intent(context, QualityActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        QualityController controller = activity.controller;
        byte[] good = vector("relative_consistent");
        AtomicReference<String> text = new AtomicReference<>();
        try {
            ui(() -> {
                assertEquals(QualityActivity.UNAVAILABLE, activity.quality.getText().toString());
                assertFalse(controller.submit(good, 1_000_000));
                activity.enable.performClick(); activity.assess.performClick();
            });
            waitUntil(() -> { ui(() -> text.set(activity.quality.getText().toString())); return text.get().contains("Consistent claims"); });
            assertTrue(text.get().contains("SYNTHETIC QUALITY ONLY"));
            assertTrue(text.get().contains("physical integrity unverified"));
            assertTrue(text.get().contains("no sensor fusion"));
            ui(() -> {
                activity.revoke.performClick(); assertEquals(QualityActivity.UNAVAILABLE, activity.quality.getText().toString());
                // A fresh displayed result must be cleared by actual background
                // lifecycle, independently of the explicit revocation above.
                activity.enable.performClick(); activity.assess.performClick();
            });
            waitUntil(() -> { ui(() -> text.set(activity.quality.getText().toString())); return text.get().contains("Consistent claims"); });
            ui(() -> assertTrue(activity.moveTaskToBack(true)));
            waitUntil(() -> activity.stops >= 1);
            ui(() -> assertEquals(QualityActivity.UNAVAILABLE, activity.quality.getText().toString()));
            ui(() -> context.startActivity(new Intent(context, QualityActivity.class)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)));
            waitUntil(() -> activity.starts >= 2);
            ui(() -> assertFalse(controller.submit(good, 1_000_000)));
        } finally { ui(activity::finish); }
        waitUntil(activity::isDestroyed);
        assertTrue(controller.awaitTermination(3000));
    }
}
