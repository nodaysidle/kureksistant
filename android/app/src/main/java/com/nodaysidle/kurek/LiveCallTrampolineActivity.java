package com.nodaysidle.kurek;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Bundle;

import androidx.core.content.ContextCompat;

/** Invisible hop for the widget: toggles the live call, or opens the app when it can't. */
public class LiveCallTrampolineActivity extends Activity {
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        boolean canRun = LiveCallService.isConfigured(this)
                && ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
                == PackageManager.PERMISSION_GRANTED;
        if (!canRun) {
            startActivity(new Intent(this, MainActivity.class));
        } else if (LiveCallService.getState() == LiveCallService.State.IDLE) {
            // Started from a visible activity so the microphone foreground service is allowed on Android 14.
            ContextCompat.startForegroundService(this, LiveCallService.startIntent(this));
        } else {
            startService(LiveCallService.stopIntent(this));
        }
        finish();
        overridePendingTransition(0, 0);
    }
}
