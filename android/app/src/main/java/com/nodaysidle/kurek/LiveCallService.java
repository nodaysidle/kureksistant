package com.nodaysidle.kurek;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ServiceInfo;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.SystemClock;

import androidx.annotation.Nullable;
import androidx.core.app.NotificationCompat;
import androidx.core.app.ServiceCompat;

import java.util.concurrent.CopyOnWriteArrayList;

/**
 * Owns the single live voice call so it survives the activity and can be driven from the widget.
 * Not exported; callers use explicit intents.
 */
public class LiveCallService extends Service {
    public enum State { IDLE, CONNECTING, LIVE }

    public interface Listener {
        void onStateChanged(State state);
        void onUserText(String text);
        void onKurekText(String text);
        void onTool(String description);
        void onError(String message);
    }

    public static final String ACTION_START = "com.nodaysidle.kurek.action.LIVE_START";
    public static final String ACTION_STOP = "com.nodaysidle.kurek.action.LIVE_STOP";

    static final String PREFS_NAME = "kurek_prefs";
    private static final String KEY_HOST = "kurek_host";
    private static final String KEY_PORT = "kurek_port";
    private static final String KEY_TOKEN = "kurek_token";
    private static final String CHANNEL_ID = "live_call";
    private static final int NOTIFICATION_ID = 1;

    private static final CopyOnWriteArrayList<Listener> LISTENERS = new CopyOnWriteArrayList<>();
    private static final Handler MAIN = new Handler(Looper.getMainLooper());
    private static volatile State state = State.IDLE;
    private static volatile long liveBaseRealtime;

    private LiveVoiceSession session;

    public static State getState() {
        return state;
    }

    /** SystemClock.elapsedRealtime() when the call went live, for Chronometer bases. */
    public static long getLiveBase() {
        return liveBaseRealtime;
    }

    public static void addListener(Listener l) {
        LISTENERS.addIfAbsent(l);
    }

    public static void removeListener(Listener l) {
        LISTENERS.remove(l);
    }

    public static boolean isConfigured(Context context) {
        SharedPreferences sp = context.getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        String host = sp.getString(KEY_HOST, "");
        String token = sp.getString(KEY_TOKEN, "");
        return host != null && !host.trim().isEmpty() && token != null && !token.trim().isEmpty();
    }

    public static Intent startIntent(Context context) {
        return new Intent(context, LiveCallService.class).setAction(ACTION_START);
    }

    public static Intent stopIntent(Context context) {
        return new Intent(context, LiveCallService.class).setAction(ACTION_STOP);
    }

    private static void setState(Context context, State next) {
        state = next;
        if (next == State.LIVE) liveBaseRealtime = SystemClock.elapsedRealtime();
        KurekLiveWidget.updateAll(context);
        for (Listener l : LISTENERS) l.onStateChanged(next);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent != null ? intent.getAction() : null;
        if (ACTION_STOP.equals(action)) {
            if (session != null) {
                session.stop();
            } else {
                finishCall();
            }
            return START_NOT_STICKY;
        }
        // startForegroundService() requires startForeground() within seconds, so do it first.
        ServiceCompat.startForeground(this, NOTIFICATION_ID, buildNotification(),
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE);
        if (!ACTION_START.equals(action) || session != null) {
            if (session == null) finishCall();
            return START_NOT_STICKY;
        }
        SharedPreferences sp = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        String host = sp.getString(KEY_HOST, "");
        String token = sp.getString(KEY_TOKEN, "");
        if (!isConfigured(this)) {
            finishCall();
            return START_NOT_STICKY;
        }
        setState(this, State.CONNECTING);
        session = new LiveVoiceSession(this, new LiveVoiceSession.Listener() {
            @Override
            public void onLive() {
                setState(LiveCallService.this, State.LIVE);
            }

            @Override
            public void onUserText(String text) {
                for (Listener l : LISTENERS) l.onUserText(text);
            }

            @Override
            public void onKurekText(String text) {
                for (Listener l : LISTENERS) l.onKurekText(text);
            }

            @Override
            public void onTool(String description) {
                for (Listener l : LISTENERS) l.onTool(description);
            }

            @Override
            public void onError(String message) {
                for (Listener l : LISTENERS) l.onError(message);
            }

            @Override
            public void onClosed() {
                session = null;
                finishCall();
            }
        });
        // Live voice listens on the HTTP port + 1 (daemon default 8791).
        session.start(host, sp.getInt(KEY_PORT, 8790) + 1, token);
        return START_NOT_STICKY;
    }

    private void finishCall() {
        setState(this, State.IDLE);
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE);
        stopSelf();
    }

    private Notification buildNotification() {
        NotificationManager nm = getSystemService(NotificationManager.class);
        nm.createNotificationChannel(new NotificationChannel(CHANNEL_ID,
                getString(R.string.live_channel), NotificationManager.IMPORTANCE_LOW));
        PendingIntent open = PendingIntent.getActivity(this, 0,
                new Intent(this, MainActivity.class), PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        PendingIntent end = PendingIntent.getService(this, 1, stopIntent(this),
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        return new NotificationCompat.Builder(this, CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_mic)
                .setContentTitle(getString(R.string.live_notification))
                .setUsesChronometer(true)
                .setWhen(System.currentTimeMillis())
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setCategory(NotificationCompat.CATEGORY_CALL)
                .setContentIntent(open)
                .addAction(0, getString(R.string.live_end), end)
                .build();
    }

    @Override
    public void onDestroy() {
        LiveVoiceSession s = session;
        session = null;
        if (s != null) s.stop();
        if (state != State.IDLE) MAIN.post(() -> setState(getApplicationContext(), State.IDLE));
        super.onDestroy();
    }

    @Nullable
    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
