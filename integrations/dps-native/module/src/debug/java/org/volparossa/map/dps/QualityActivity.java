// SPDX-License-Identifier: GPL-3.0-only
package org.volparossa.map.dps;

import android.app.Activity;
import android.os.Bundle;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;
import java.io.InputStream;
import java.io.IOException;

/** Nonexported debug fixture. No OS location, sensor, recording or network API. */
public final class QualityActivity extends Activity {
    static final String UNAVAILABLE = "Synthetic DPS quality unavailable. Disabled until explicit consent.";
    QualityController controller;
    TextView quality;
    Button enable, assess, revoke;
    volatile int starts, stops;
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        quality = new TextView(this); quality.setText(UNAVAILABLE);
        controller = new QualityController(q -> quality.setText(q == null ? UNAVAILABLE : "SYNTHETIC QUALITY ONLY\n" + q.display()));
        enable = new Button(this);
        enable.setText("Consent to local synthetic quality for 30 seconds");
        enable.setOnClickListener(v -> controller.enable(true, QualityController.MAX_TTL_MS));
        revoke = new Button(this); revoke.setText("Revoke and clear quality");
        revoke.setOnClickListener(v -> controller.revoke());
        assess = new Button(this); assess.setText("Assess public synthetic claims locally");
        assess.setOnClickListener(v -> {
            try (InputStream input = getAssets().open("relative_consistent.pb")) {
                byte[] bytes = new byte[NativeQuality.MAX_INPUT + 1];
                int used = 0, count;
                while (used < bytes.length && (count = input.read(bytes, used, bytes.length - used)) != -1) used += count;
                if (used > 0 && used <= NativeQuality.MAX_INPUT)
                    controller.submit(java.util.Arrays.copyOf(bytes, used), 1_000_000);
                java.util.Arrays.fill(bytes, (byte)0);
            } catch (IOException ignored) { controller.revoke(); }
        });
        root.addView(quality); root.addView(enable); root.addView(assess); root.addView(revoke); setContentView(root);
    }
    @Override public void onStart() { super.onStart(); ++starts; controller.foreground(); }
    @Override public void onStop() { controller.background(); ++stops; super.onStop(); }
    @Override public void onDestroy() { controller.close(); super.onDestroy(); }
}
