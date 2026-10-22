// SPDX-License-Identifier: GPL-3.0-only
package org.volparossa.map.dps;

import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import java.util.Arrays;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/** App-owned authority for transient quality only. One worker for its entire lifetime. */
public final class QualityController implements AutoCloseable {
    public interface Clock { long elapsedMs(); }
    public interface Assessor { NativeQuality.Result assess(byte[] request, long nowUnixMs); }
    public interface View { void show(QualitySummary quality); }
    interface PublicationObserver { void posted(); default void completed() {} }
    public static final long MAX_TTL_MS = 30_000;
    private final Object lock = new Object();
    private final Handler main = new Handler(Looper.getMainLooper());
    private final Object callbacks = new Object();
    private final Clock clock;
    private final Assessor assessor;
    private final View view;
    private final PublicationObserver publicationObserver;
    private final Thread worker;
    private final CountDownLatch terminated = new CountDownLatch(1);
    private boolean foreground, enabled, closed, busy, running;
    private long generation, deadline;
    private Task pending;
    private static final class Task {
        final long generation, now;
        final byte[] bytes;
        Task(long generation, long now, byte[] bytes) {
            this.generation = generation; this.now = now; this.bytes = bytes;
        }
        void erase() { Arrays.fill(bytes, (byte)0); }
    }

    public QualityController(View view) { this(view, SystemClock::elapsedRealtime, NativeQuality::assess); }
    QualityController(View view, Clock clock, Assessor assessor) { this(view, clock, assessor, null); }
    QualityController(View view, Clock clock, Assessor assessor, PublicationObserver observer) {
        this.view = view; this.clock = clock; this.assessor = assessor; this.publicationObserver = observer;
        worker = new Thread(this::runWorker, "dps-quality-owned");
        worker.start();
    }
    private void ui() {
        if (Looper.myLooper() != Looper.getMainLooper()) throw new IllegalStateException("quality_ui_thread");
    }
    private boolean authorized(long g) {
        long now = clock.elapsedMs();
        return !closed && foreground && enabled && g == generation && now >= 0 && now < deadline;
    }
    public void foreground() { ui(); synchronized (lock) { if (!closed) foreground = true; } }
    /** Explicit choice; resume never silently grants a new generation. */
    public boolean enable(boolean consent, long ttlMs) {
        ui();
        revoke();
        synchronized (lock) {
            long now = clock.elapsedMs();
            if (closed || !foreground || !consent || ttlMs < 1 || ttlMs > MAX_TTL_MS
                    || now < 0 || now > Long.MAX_VALUE - ttlMs) return false;
            enabled = true; deadline = now + ttlMs;
            final long g = generation;
            main.postAtTime(() -> expire(g), callbacks, SystemClock.uptimeMillis() + ttlMs);
            return true;
        }
    }
    private void expire(long g) {
        ui();
        synchronized (lock) {
            if (g != generation || closed || !enabled) return;
            if (authorized(g)) {
                main.postAtTime(() -> expire(g), callbacks,
                        SystemClock.uptimeMillis() + Math.max(1, deadline - clock.elapsedMs()));
                return;
            }
        }
        revoke();
    }
    public boolean submit(byte[] request, long nowUnixMs) {
        ui();
        synchronized (lock) {
            if (!authorized(generation) || busy || request == null || request.length < 1
                    || request.length > NativeQuality.MAX_INPUT || nowUnixMs <= 0) return false;
            busy = true;
            pending = new Task(generation, nowUnixMs, request.clone());
            lock.notifyAll();
            return true;
        }
    }
    public void revoke() {
        ui();
        synchronized (lock) {
            enabled = false; deadline = 0; ++generation;
            if (pending != null) { pending.erase(); pending = null; }
            busy = running;
            main.removeCallbacksAndMessages(callbacks);
        }
        view.show(null);
    }
    public void disable() { revoke(); }
    public void background() {
        ui(); synchronized (lock) { foreground = false; }
        revoke();
    }
    @Override public void close() {
        ui(); revoke();
        synchronized (lock) { closed = true; foreground = false; lock.notifyAll(); }
        // Native synchronous work cannot be forcibly cancelled safely. Publication
        // is already revoked; the same sole worker exits when that work returns.
        worker.interrupt();
    }
    /** Observer/test only: joining the worker never blocks the Android UI. */
    public boolean awaitTermination(long timeoutMs) throws InterruptedException {
        if (Looper.myLooper() == Looper.getMainLooper()) throw new IllegalStateException("quality_join_ui");
        if (timeoutMs < 1 || timeoutMs > 5000) throw new IllegalArgumentException("quality_join_bound");
        long start = System.nanoTime();
        if (!terminated.await(timeoutMs, TimeUnit.MILLISECONDS)) return false;
        long remaining = TimeUnit.MILLISECONDS.toNanos(timeoutMs) - (System.nanoTime() - start);
        if (remaining > 0) worker.join(Math.max(1, TimeUnit.NANOSECONDS.toMillis(remaining)));
        return !worker.isAlive();
    }
    private void runWorker() {
        try {
            while (true) {
                Task task;
                synchronized (lock) {
                    while (!closed && pending == null) {
                        try { lock.wait(); } catch (InterruptedException ignored) { /* Recheck ownership. */ }
                    }
                    if (closed) return;
                    task = pending; pending = null; running = true;
                }
                NativeQuality.Result result = null;
                final boolean evaluate;
                synchronized (lock) { evaluate = authorized(task.generation); }
                try {
                    if (evaluate) result = assessor.assess(task.bytes, task.now);
                } catch (RuntimeException | LinkageError ignored) {
                    result = NativeQuality.Result.failure(5);
                } finally {
                    task.erase();
                }
                final NativeQuality.Result ready = result;
                synchronized (lock) {
                    running = false;
                    if (!evaluate || !authorized(task.generation)) {
                        busy = false;
                        if (publicationObserver != null) publicationObserver.completed();
                        continue;
                    }
                    // Publication and all revocations execute on the same UI thread.
                    main.postAtTime(() -> {
                        synchronized (lock) {
                            if (!authorized(task.generation)) { busy = false; return; }
                            busy = false;
                        }
                        view.show(ready != null && ready.error == 0 ? ready.quality : null);
                    }, callbacks, SystemClock.uptimeMillis());
                    // Package-private test observer runs only after actual posting.
                    // Production construction supplies null and changes no authority.
                    if (publicationObserver != null) { publicationObserver.posted(); publicationObserver.completed(); }
                }
            }
        } finally { terminated.countDown(); }
    }
}
