// SPDX-License-Identifier: GPL-3.0-only
package org.volparossa.map.dps;

/** Claims and pair consistency only. No coordinates, identities or timestamps. */
public final class QualitySummary {
    public final int abiVersion, dpsVersion, status, frameKind;
    public final int observationCount, eligibleCount, excludedCount, conflictCount, limitationsMask;

    private QualitySummary(int[] f) {
        abiVersion = f[0]; dpsVersion = f[1]; status = f[2]; frameKind = f[3];
        observationCount = f[4]; eligibleCount = f[5]; excludedCount = f[6];
        conflictCount = f[7]; limitationsMask = f[8];
    }

    static QualitySummary parse(int[] input) {
        if (input == null || input.length != 9) throw new IllegalArgumentException("quality_shape");
        int[] f = input.clone();
        for (int v : f) if (v < 0) throw new IllegalArgumentException("quality_range");
        if (f[0] != 1 || f[1] != 1 || f[2] > 3 || (f[3] != 1 && f[3] != 2)
                || f[4] > 32 || f[5] > f[4] || f[6] != f[4] - f[5] || f[8] != 0x7f
                || f[7] > f[5] * (f[5] - 1) / 2
                || (f[2] == 0 && (f[5] != 0 || f[7] != 0))
                || (f[2] == 1 && (f[5] != 1 || f[7] != 0))
                || (f[2] == 2 && (f[5] < 2 || f[7] != 0))
                || (f[2] == 3 && (f[5] < 2 || f[7] == 0)))
            throw new IllegalArgumentException("quality_contract");
        return new QualitySummary(f);
    }

    public String display() {
        String[] states = {"Unavailable", "Single claim", "Consistent claims", "Conflicting claims"};
        return states[status] + "; caller-asserted "
                + (frameKind == 1 ? "ECEF" : "local Cartesian")
                + "; " + eligibleCount + " eligible claims; " + conflictCount + " conflicting pairs.\n"
                + "Caller-supplied evidence; physical integrity unverified; physical independence unverified; "
                + "uncertainty confidence unknown; no sensor fusion; no sensor or clock verification; "
                + "pairwise consistency only. No position or accuracy improvement established.";
    }
}
