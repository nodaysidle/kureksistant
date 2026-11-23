package com.nodaysidle.kurek;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ServiceInfo;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.os.IBinder;
import android.provider.MediaStore;

import androidx.annotation.Nullable;
import androidx.core.app.NotificationCompat;
import androidx.core.app.ServiceCompat;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/**
 * Lightweight background poller that receives camera arrival alerts from the
 * Kurek daemon over Tailscale (/remote/alerts), saves each snapshot to
 * Pictures/doorcam/, and posts a native Android notification with the photo.
 */
public class CameraAlertService extends Service {
    private static final String PREFS_NAME = "kurek_prefs";
    private static final String KEY_HOST = "kurek_host";
    private static final String KEY_PORT = "kurek_port";
    private static final String KEY_TOKEN = "kurek_token";
    private static final String KEY_LAST_ALERT_ID = "kurek_last_alert_id_v2";

    private static final String CHANNEL_MONITOR = "camera_monitor";
    private static final String CHANNEL_ALERTS = "camera_alerts";
    private static final int MONITOR_NOTIFICATION_ID = 20;
    private static final int ALERT_NOTIFICATION_BASE = 200;
    private static final long POLL_INTERVAL_MS = 10_000L;

    private volatile boolean running = false;
    private Thread pollThread;

    public static void startIfConfigured(Context context) {
        if (!LiveCallService.isConfigured(context)) {
            return;
        }
        Intent intent = new Intent(context, CameraAlertService.class);
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent);
            } else {
                context.startService(intent);
            }
        } catch (Exception ignored) {
            // Foreground service restrictions when launched in restricted background states.
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        ensureChannels();
        ServiceCompat.startForeground(
                this,
                MONITOR_NOTIFICATION_ID,
                buildMonitorNotification(),
                ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC
        );
        if (!running) {
            running = true;
            pollThread = new Thread(this::pollLoop, "kurek-alert-poller");
            pollThread.start();
        }
        return START_STICKY;
    }

    private void pollLoop() {
        while (running) {
            try {
                pollOnce();
            } catch (Exception ignored) {
                // Transient Tailscale / Wi-Fi hiccups are retried on the next tick.
            }
            try {
                Thread.sleep(POLL_INTERVAL_MS);
            } catch (InterruptedException e) {
                break;
            }
        }
    }

    private void pollOnce() throws Exception {
        SharedPreferences sp = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        String host = sp.getString(KEY_HOST, "");
        int port = sp.getInt(KEY_PORT, 8790);
        String token = sp.getString(KEY_TOKEN, "");
        if (host == null || host.trim().isEmpty() || token == null || token.trim().isEmpty()) {
            return;
        }
        long lastId = sp.getLong(KEY_LAST_ALERT_ID, 0L);

        URL url = new URL("http://" + host.trim() + ":" + port + "/remote/alerts?since=" + lastId);
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setConnectTimeout(3000);
        conn.setReadTimeout(4000);
        conn.setRequestMethod("GET");
        conn.setRequestProperty("Authorization", "Bearer " + token.trim());
        try {
            if (conn.getResponseCode() != 200) {
                return;
            }
            StringBuilder sb = new StringBuilder();
            try (BufferedReader br = new BufferedReader(new InputStreamReader(conn.getInputStream()))) {
                String line;
                while ((line = br.readLine()) != null) {
                    sb.append(line);
                }
            }
            JSONObject obj = new JSONObject(sb.toString());
            JSONArray alerts = obj.optJSONArray("alerts");
            if (alerts == null || alerts.length() == 0) {
                return;
            }
            long maxId = lastId;
            JSONObject latest = null;
            for (int i = 0; i < alerts.length(); i++) {
                JSONObject item = alerts.optJSONObject(i);
                if (item == null) continue;
                long id = item.optLong("id", 0L);
                if (id > maxId) {
                    maxId = id;
                    latest = item;
                }
            }
            if (latest != null) {
                sp.edit().putLong(KEY_LAST_ALERT_ID, maxId).apply();
                byte[] jpegBytes = latest.optBoolean("has_snapshot", false)
                        ? fetchSnapshotBytes(host.trim(), port, token.trim())
                        : null;
                Bitmap snapshot = null;
                if (jpegBytes != null && jpegBytes.length > 0) {
                    String label = latest.optString("label", "car");
                    saveSnapshotToDoorcam(jpegBytes, label);
                    snapshot = BitmapFactory.decodeByteArray(jpegBytes, 0, jpegBytes.length);
                }
                postAlertNotification(
                        maxId,
                        latest.optString("camera", "Entrance"),
                        latest.optString("message", "Vehicle detected."),
                        snapshot
                );
            }
        } finally {
            conn.disconnect();
        }
    }

    @Nullable
    private byte[] fetchSnapshotBytes(String host, int port, String token) {
        HttpURLConnection conn = null;
        try {
            URL url = new URL("http://" + host + ":" + port + "/remote/camera/snapshot");
            conn = (HttpURLConnection) url.openConnection();
            conn.setConnectTimeout(3000);
            conn.setReadTimeout(5000);
            conn.setRequestMethod("GET");
            conn.setRequestProperty("Authorization", "Bearer " + token);
            if (conn.getResponseCode() != 200) {
                return null;
            }
            try (InputStream is = conn.getInputStream();
                 ByteArrayOutputStream bos = new ByteArrayOutputStream()) {
                byte[] buf = new byte[8192];
                int n;
                while ((n = is.read(buf)) > 0) {
                    bos.write(buf, 0, n);
                }
                return bos.toByteArray();
            }
        } catch (Exception ignored) {
            return null;
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    private void saveSnapshotToDoorcam(byte[] jpegBytes, String label) {
        String stamp = new SimpleDateFormat("yyyy-MM-dd_HH-mm-ss", Locale.US).format(new Date());
        String filename = stamp + "_" + label + ".jpg";
        try {
            ContentResolver cr = getContentResolver();
            ContentValues values = new ContentValues();
            values.put(MediaStore.Images.Media.DISPLAY_NAME, filename);
            values.put(MediaStore.Images.Media.MIME_TYPE, "image/jpeg");
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                values.put(
                        MediaStore.Images.Media.RELATIVE_PATH,
                        Environment.DIRECTORY_PICTURES + "/doorcam"
                );
                values.put(MediaStore.Images.Media.IS_PENDING, 1);
            }
            Uri uri = cr.insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values);
            if (uri == null) return;
            try (OutputStream os = cr.openOutputStream(uri)) {
                if (os != null) {
                    os.write(jpegBytes);
                }
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                ContentValues done = new ContentValues();
                done.put(MediaStore.Images.Media.IS_PENDING, 0);
                cr.update(uri, done, null, null);
            }
        } catch (Exception ignored) {
            // Never fail the notification if gallery storage is temporarily unavailable.
        }
    }

    private void postAlertNotification(long id, String camera, String message, @Nullable Bitmap snap) {
        Intent openIntent = new Intent(this, MainActivity.class)
                .setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        int reqCode = (int) (id % 100000L);
        PendingIntent pi = PendingIntent.getActivity(
                this, reqCode, openIntent, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );
        NotificationCompat.Builder b = new NotificationCompat.Builder(this, CHANNEL_ALERTS)
                .setSmallIcon(R.drawable.ic_mic)
                .setContentTitle("Kurek - " + camera)
                .setContentText(message)
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setCategory(NotificationCompat.CATEGORY_ALARM)
                .setAutoCancel(true)
                .setContentIntent(pi);
        if (snap != null) {
            b.setLargeIcon(snap);
            b.setStyle(new NotificationCompat.BigPictureStyle()
                    .bigPicture(snap)
                    .bigLargeIcon((Bitmap) null));
        }
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (nm != null) {
            nm.notify(ALERT_NOTIFICATION_BASE + (int) (id % 20L), b.build());
        }
    }

    private Notification buildMonitorNotification() {
        Intent openIntent = new Intent(this, MainActivity.class)
                .setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent pi = PendingIntent.getActivity(
                this, 0, openIntent, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );
        return new NotificationCompat.Builder(this, CHANNEL_MONITOR)
                .setSmallIcon(R.drawable.ic_mic)
                .setContentTitle("Kurek alerts active")
                .setContentText("Watching entrance camera over Tailscale")
                .setPriority(NotificationCompat.PRIORITY_MIN)
                .setSilent(true)
                .setOngoing(true)
                .setContentIntent(pi)
                .build();
    }

    private void ensureChannels() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return;
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (nm == null) return;
        NotificationChannel mon = new NotificationChannel(
                CHANNEL_MONITOR, "Kurek Monitor", NotificationManager.IMPORTANCE_MIN
        );
        mon.setShowBadge(false);
        nm.createNotificationChannel(mon);

        NotificationChannel alerts = new NotificationChannel(
                CHANNEL_ALERTS, "Kurek Camera Alerts", NotificationManager.IMPORTANCE_HIGH
        );
        alerts.enableVibration(true);
        nm.createNotificationChannel(alerts);
    }

    @Override
    public void onDestroy() {
        running = false;
        if (pollThread != null) {
            pollThread.interrupt();
        }
        super.onDestroy();
    }

    @Nullable
    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
