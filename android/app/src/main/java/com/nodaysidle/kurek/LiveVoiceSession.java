package com.nodaysidle.kurek;

import android.annotation.SuppressLint;
import android.content.Context;
import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioRecord;
import android.media.AudioTrack;
import android.media.MediaRecorder;
import android.media.audiofx.AcousticEchoCanceler;
import android.os.Handler;
import android.os.Looper;

import org.json.JSONObject;

import java.util.concurrent.TimeUnit;

import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import okhttp3.WebSocket;
import okhttp3.WebSocketListener;
import okio.ByteString;

/**
 * Live voice over the daemon's /live WebSocket: mic PCM16 16 kHz up, PCM16 24 kHz down.
 * Talk-only; the daemon relays to xAI so the xAI key stays on the PC.
 */
public class LiveVoiceSession {
    public interface Listener {
        void onLive();
        void onUserText(String text);
        void onKurekText(String text);
        void onError(String message);
        void onClosed();
    }

    private static final int IN_RATE = 16000;
    private static final int OUT_RATE = 24000;
    private static final int CHUNK_BYTES = IN_RATE * 2 / 50; // 20 ms

    private final Context context;
    private final Listener listener;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final OkHttpClient http = new OkHttpClient.Builder()
            .pingInterval(20, TimeUnit.SECONDS)
            .readTimeout(0, TimeUnit.MILLISECONDS)
            .build();

    private WebSocket ws;
    private AudioRecord recorder;
    private AudioTrack track;
    private AcousticEchoCanceler aec;
    private Thread micThread;
    private volatile boolean running;
    private int savedAudioMode;

    public LiveVoiceSession(Context context, Listener listener) {
        this.context = context.getApplicationContext();
        this.listener = listener;
    }

    public void start(String host, int port, String token) {
        Request req = new Request.Builder()
                .url("ws://" + host + ":" + port + "/live")
                .header("Authorization", "Bearer " + token)
                .build();
        ws = http.newWebSocket(req, new WebSocketListener() {
            @Override
            public void onMessage(WebSocket webSocket, String text) {
                handleEvent(text);
            }

            @Override
            public void onMessage(WebSocket webSocket, ByteString bytes) {
                AudioTrack t = track;
                if (t != null) {
                    byte[] pcm = bytes.toByteArray();
                    t.write(pcm, 0, pcm.length);
                }
            }

            @Override
            public void onFailure(WebSocket webSocket, Throwable t, Response response) {
                int code = response != null ? response.code() : 0;
                String why;
                if (code == 401) {
                    why = "Token rejected (401)";
                } else if (code == 404) {
                    why = "Live voice is off on the PC (set KUREK_LIVE_VOICE=1)";
                } else {
                    why = "Can't reach live voice: " + t.getMessage();
                }
                main.post(() -> listener.onError(why));
                stop();
            }

            @Override
            public void onClosed(WebSocket webSocket, int code, String reason) {
                stop();
            }
        });
    }

    private void handleEvent(String text) {
        try {
            JSONObject ev = new JSONObject(text);
            switch (ev.optString("type")) {
                case "session.updated":
                    startAudio();
                    main.post(listener::onLive);
                    break;
                case "input_audio_buffer.speech_started":
                    // Barge-in: drop whatever Kurek was still saying.
                    AudioTrack t = track;
                    if (t != null) {
                        t.pause();
                        t.flush();
                        t.play();
                    }
                    break;
                case "conversation.item.input_audio_transcription.completed": {
                    String s = ev.optString("transcript").trim();
                    if (!s.isEmpty()) main.post(() -> listener.onUserText(s));
                    break;
                }
                case "response.output_audio_transcript.done": {
                    String s = ev.optString("transcript").trim();
                    if (!s.isEmpty()) main.post(() -> listener.onKurekText(s));
                    break;
                }
                case "error": {
                    JSONObject err = ev.optJSONObject("error");
                    String msg = err != null ? err.optString("message", text) : text;
                    main.post(() -> listener.onError(msg));
                    break;
                }
                default:
                    break;
            }
        } catch (Exception ignored) {
            // Malformed event: skip it, audio keeps flowing.
        }
    }

    @SuppressLint("MissingPermission") // caller checks RECORD_AUDIO before start()
    private void startAudio() {
        if (running) return;
        AudioManager am = (AudioManager) context.getSystemService(Context.AUDIO_SERVICE);
        savedAudioMode = am.getMode();
        // Communication mode turns on the platform echo canceller and routes to the loudspeaker.
        am.setMode(AudioManager.MODE_IN_COMMUNICATION);
        am.setSpeakerphoneOn(true);

        int outMin = AudioTrack.getMinBufferSize(OUT_RATE, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT);
        track = new AudioTrack.Builder()
                .setAudioAttributes(new AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                        .build())
                .setAudioFormat(new AudioFormat.Builder()
                        .setSampleRate(OUT_RATE)
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                        .build())
                .setBufferSizeInBytes(Math.max(outMin, OUT_RATE)) // ~0.5 s
                .setTransferMode(AudioTrack.MODE_STREAM)
                .build();
        track.play();

        int inMin = AudioRecord.getMinBufferSize(IN_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
        recorder = new AudioRecord(MediaRecorder.AudioSource.VOICE_COMMUNICATION, IN_RATE,
                AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, Math.max(inMin, CHUNK_BYTES * 4));
        if (AcousticEchoCanceler.isAvailable()) {
            aec = AcousticEchoCanceler.create(recorder.getAudioSessionId());
            if (aec != null) aec.setEnabled(true);
        }
        recorder.startRecording();
        running = true;
        micThread = new Thread(() -> {
            byte[] buf = new byte[CHUNK_BYTES];
            while (running) {
                int n = recorder.read(buf, 0, buf.length);
                WebSocket socket = ws;
                if (n > 0 && socket != null) socket.send(ByteString.of(buf, 0, n));
            }
        }, "kurek-live-mic");
        micThread.start();
    }

    public synchronized void stop() {
        boolean wasActive = ws != null;
        running = false;
        if (ws != null) {
            ws.close(1000, "bye");
            ws = null;
        }
        if (micThread != null) {
            try {
                micThread.join(300);
            } catch (InterruptedException ignored) {
                Thread.currentThread().interrupt();
            }
            micThread = null;
        }
        if (recorder != null) {
            recorder.stop();
            recorder.release();
            recorder = null;
        }
        if (aec != null) {
            aec.release();
            aec = null;
        }
        if (track != null) {
            track.stop();
            track.release();
            track = null;
            AudioManager am = (AudioManager) context.getSystemService(Context.AUDIO_SERVICE);
            am.setSpeakerphoneOn(false);
            am.setMode(savedAudioMode);
        }
        if (wasActive) main.post(listener::onClosed);
    }
}
