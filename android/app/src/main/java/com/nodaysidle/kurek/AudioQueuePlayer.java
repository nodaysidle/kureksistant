package com.nodaysidle.kurek;

import android.content.Context;
import android.media.MediaPlayer;
import java.io.File;
import java.io.FileOutputStream;
import java.util.LinkedList;
import java.util.Queue;

public class AudioQueuePlayer {
    private final Context context;
    private final Queue<byte[]> audioQueue = new LinkedList<>();
    private MediaPlayer mediaPlayer;
    private boolean isPlaying = false;
    private File currentTempFile = null;

    public AudioQueuePlayer(Context context) {
        this.context = context.getApplicationContext();
    }

    public synchronized void enqueue(byte[] audioBytes) {
        if (audioBytes == null || audioBytes.length == 0) return;
        audioQueue.add(audioBytes);
        if (!isPlaying) {
            playNext();
        }
    }

    private synchronized void playNext() {
        if (audioQueue.isEmpty()) {
            isPlaying = false;
            cleanCurrentFile();
            return;
        }

        isPlaying = true;
        cleanCurrentFile();
        byte[] bytes = audioQueue.poll();

        try {
            currentTempFile = File.createTempFile("kurek_sal_", ".mp3", context.getCacheDir());
            currentTempFile.deleteOnExit();
            try (FileOutputStream fos = new FileOutputStream(currentTempFile)) {
                fos.write(bytes);
            }

            if (mediaPlayer != null) {
                try {
                    mediaPlayer.reset();
                    mediaPlayer.release();
                } catch (Exception ignored) {}
            }

            mediaPlayer = new MediaPlayer();
            mediaPlayer.setDataSource(currentTempFile.getAbsolutePath());
            mediaPlayer.setOnCompletionListener(mp -> {
                synchronized (AudioQueuePlayer.this) {
                    playNext();
                }
            });
            mediaPlayer.setOnErrorListener((mp, what, extra) -> {
                synchronized (AudioQueuePlayer.this) {
                    playNext();
                }
                return true;
            });
            mediaPlayer.prepare();
            mediaPlayer.start();
        } catch (Exception e) {
            isPlaying = false;
            playNext();
        }
    }

    private void cleanCurrentFile() {
        if (currentTempFile != null) {
            try {
                if (currentTempFile.exists()) {
                    currentTempFile.delete();
                }
            } catch (Exception ignored) {}
            currentTempFile = null;
        }
    }

    public synchronized void stop() {
        audioQueue.clear();
        isPlaying = false;
        if (mediaPlayer != null) {
            try {
                if (mediaPlayer.isPlaying()) {
                    mediaPlayer.stop();
                }
                mediaPlayer.reset();
                mediaPlayer.release();
            } catch (Exception ignored) {}
            mediaPlayer = null;
        }
        cleanCurrentFile();
    }
}
