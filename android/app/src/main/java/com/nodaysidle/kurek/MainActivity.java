package com.nodaysidle.kurek;

import android.Manifest;
import android.graphics.Color;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.os.Vibrator;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import android.speech.tts.TextToSpeech;
import android.view.LayoutInflater;
import android.view.View;
import android.view.inputmethod.EditorInfo;
import android.widget.EditText;
import android.widget.ImageButton;
import android.widget.TextView;
import android.widget.Toast;
import androidx.annotation.NonNull;
import androidx.activity.EdgeToEdge;
import androidx.activity.SystemBarStyle;
import androidx.appcompat.app.AlertDialog;
import androidx.appcompat.app.AppCompatActivity;
import androidx.appcompat.widget.SwitchCompat;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;
import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowInsetsCompat;
import androidx.recyclerview.widget.LinearLayoutManager;
import androidx.recyclerview.widget.RecyclerView;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Locale;

public class MainActivity extends AppCompatActivity {
    private static final int REQ_RECORD_AUDIO = 1001;
    private static final int REQ_LIVE_AUDIO = 1002;
    private static final String PREFS_NAME = "kurek_prefs";
    private static final String KEY_HOST = "kurek_host";
    private static final String KEY_PORT = "kurek_port";
    private static final String KEY_TOKEN = "kurek_token";
    private static final String KEY_ASKED_NOTIFICATIONS = "kurek_asked_notifications";
    private static final String KEY_SPEAK_OUT_LOUD = "kurek_speak_out_loud";

    private RecyclerView recyclerChat;
    private ChatAdapter chatAdapter;
    private List<ChatMessage> messageList;
    private EditText editPrompt;
    private ImageButton btnSend, btnMic, btnSettings, btnSpeaker;
    private TextView txtStatus, txtHostLabel;
    private View statusDot;
    private Vibrator vibrator;

    private SpeechRecognizer speechRecognizer;
    private boolean isListeningSpeech = false;

    private TextToSpeech tts;
    private boolean isTtsInitialized = false;
    private boolean speakOutLoud = false;
    private AudioQueuePlayer audioPlayer;

    private final KurekClient client = new KurekClient();
    private TextView chipLive;
    private String host = "";
    private int port = 8790;
    private String token = "";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        // The theme is dark whatever the system setting is, so pin light system-bar icons
        // (SystemBarStyle.auto would follow the system and draw dark icons on dark bars).
        EdgeToEdge.enable(this, SystemBarStyle.dark(Color.TRANSPARENT), SystemBarStyle.dark(Color.TRANSPARENT));
        if (android.os.Build.VERSION.SDK_INT >= 29) {
            getWindow().setNavigationBarContrastEnforced(false);
        }
        setContentView(R.layout.activity_main);
        applyWindowInsets();

        vibrator = (Vibrator) getSystemService(Context.VIBRATOR_SERVICE);
        audioPlayer = new AudioQueuePlayer(this);
        loadPreferences();

        initViews();
        setupChatRecycler();
        setupListeners();
        setupSpeechRecognizer();
        setupTts();

        refreshConnectionUi();
    }

    /** Top bar grows under the status bar, input bar sits above the gesture bar or the keyboard. */
    private void applyWindowInsets() {
        View root = findViewById(R.id.root);
        View header = findViewById(R.id.headerLayout);
        View input = findViewById(R.id.bottomInputLayout);
        final int headerTop = header.getPaddingTop();
        final int inputBottom = input.getPaddingBottom();
        ViewCompat.setOnApplyWindowInsetsListener(root, (v, insets) -> {
            Insets bars = insets.getInsets(
                    WindowInsetsCompat.Type.systemBars() | WindowInsetsCompat.Type.displayCutout());
            Insets ime = insets.getInsets(WindowInsetsCompat.Type.ime());
            v.setPadding(bars.left, 0, bars.right, 0);
            header.setPadding(header.getPaddingLeft(), headerTop + bars.top,
                    header.getPaddingRight(), header.getPaddingBottom());
            input.setPadding(input.getPaddingLeft(), input.getPaddingTop(),
                    input.getPaddingRight(), inputBottom + Math.max(bars.bottom, ime.bottom));
            return WindowInsetsCompat.CONSUMED;
        });
    }

    private void loadPreferences() {
        SharedPreferences sp = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        host = sp.getString(KEY_HOST, "");
        port = sp.getInt(KEY_PORT, 8790);
        token = sp.getString(KEY_TOKEN, "");
        speakOutLoud = sp.getBoolean(KEY_SPEAK_OUT_LOUD, false);
        if (host == null) host = "";
        if (token == null) token = "";
    }

    private void savePreferences(String h, int p, String t, boolean speak) {
        SharedPreferences sp = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        sp.edit().putString(KEY_HOST, h)
                .putInt(KEY_PORT, p)
                .putString(KEY_TOKEN, t)
                .putBoolean(KEY_SPEAK_OUT_LOUD, speak)
                .apply();
        this.host = h;
        this.port = p;
        this.token = t;
        this.speakOutLoud = speak;
        updateSpeakerButtonUi();
        refreshConnectionUi();
    }

    private void savePreferences(String h, int p, String t) {
        savePreferences(h, p, t, this.speakOutLoud);
    }

    private boolean isConfigured() {
        return host != null && !host.trim().isEmpty()
                && token != null && !token.trim().isEmpty();
    }

    private void refreshConnectionUi() {
        if (!isConfigured()) {
            txtHostLabel.setText(R.string.host_unset);
            setStatusState("SETUP", getColor(R.color.warn));
            return;
        }
        txtHostLabel.setText("Workstation: " + host);
        pingServer();
    }

    private void initViews() {
        recyclerChat = findViewById(R.id.recyclerChat);
        editPrompt = findViewById(R.id.editPrompt);
        btnSend = findViewById(R.id.btnSend);
        btnMic = findViewById(R.id.btnMic);
        btnSettings = findViewById(R.id.btnSettings);
        btnSpeaker = findViewById(R.id.btnSpeaker);
        txtStatus = findViewById(R.id.txtStatus);
        txtHostLabel = findViewById(R.id.txtHostLabel);
        statusDot = findViewById(R.id.statusDot);

        updateSpeakerButtonUi();

        chipLive = findViewById(R.id.chipLive);
        chipLive.setOnClickListener(v -> toggleLive());
        findViewById(R.id.btnTerminal).setOnClickListener(v -> dispatchUserPrompt("Open herdr on my workstation."));
        findViewById(R.id.chipGrokbot).setOnClickListener(v -> dispatchUserPrompt("Open Grokbot on my workstation"));
        findViewById(R.id.chipSysStatus).setOnClickListener(v -> dispatchUserPrompt("Tell me my workstation's system activity: CPU load, memory, and temperatures."));
        findViewById(R.id.chipProjects).setOnClickListener(v -> dispatchUserPrompt("List all active projects in my nodaysidle vault"));
        findViewById(R.id.chipScreen).setOnClickListener(v -> dispatchUserPrompt("Inspect what is on the workstation screen right now"));
    }

    private void setupChatRecycler() {
        messageList = new ArrayList<>();
        chatAdapter = new ChatAdapter(messageList);
        LinearLayoutManager lm = new LinearLayoutManager(this);
        lm.setStackFromEnd(true);
        recyclerChat.setLayoutManager(lm);
        recyclerChat.setAdapter(chatAdapter);

        String welcomeText = isConfigured()
                ? "Kurek Remote ready. Speak or type instructions for your Arch workstation."
                : "Set your workstation host and remote token in Settings (gear icon) before sending prompts.";
        ChatMessage welcome = new ChatMessage(ChatMessage.TYPE_KUREK, welcomeText, getTime());
        messageList.add(welcome);
        chatAdapter.notifyItemInserted(0);
    }

    private void setupListeners() {
        btnSend.setOnClickListener(v -> {
            String p = editPrompt.getText().toString().trim();
            if (!p.isEmpty()) {
                dispatchUserPrompt(p);
                editPrompt.setText("");
            }
        });

        editPrompt.setOnEditorActionListener((v, actionId, event) -> {
            if (actionId == EditorInfo.IME_ACTION_SEND) {
                btnSend.performClick();
                return true;
            }
            return false;
        });

        btnMic.setOnClickListener(v -> {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                ActivityCompat.requestPermissions(this, new String[]{Manifest.permission.RECORD_AUDIO}, REQ_RECORD_AUDIO);
                return;
            }
            toggleVoiceRecording();
        });

        btnSpeaker.setOnClickListener(v -> toggleSpeakOutLoud());
        btnSettings.setOnClickListener(v -> showSettingsDialog());
    }

    private void setupTts() {
        tts = new TextToSpeech(this, status -> {
            if (status == TextToSpeech.SUCCESS) {
                isTtsInitialized = true;
                try {
                    int res = tts.setLanguage(Locale.getDefault());
                    if (res == TextToSpeech.LANG_MISSING_DATA || res == TextToSpeech.LANG_NOT_SUPPORTED) {
                        tts.setLanguage(Locale.US);
                    }
                } catch (Exception ignored) {}
            }
        });
    }

    private void toggleSpeakOutLoud() {
        speakOutLoud = !speakOutLoud;
        SharedPreferences sp = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        sp.edit().putBoolean(KEY_SPEAK_OUT_LOUD, speakOutLoud).apply();
        updateSpeakerButtonUi();
        triggerHaptic(40);
        if (!speakOutLoud) {
            stopAudio();
            Toast.makeText(this, "xAI Voice: OFF", Toast.LENGTH_SHORT).show();
        } else {
            Toast.makeText(this, "xAI Voice (Sal): ON", Toast.LENGTH_SHORT).show();
        }
    }

    private void updateSpeakerButtonUi() {
        if (btnSpeaker != null) {
            btnSpeaker.setImageResource(speakOutLoud ? R.drawable.ic_volume_up : R.drawable.ic_volume_off);
        }
    }

    private void speakText(String text) {
        if (!speakOutLoud || !isTtsInitialized || tts == null || text == null) return;
        String clean = text.replaceAll("[*#`_~]", "").trim();
        if (clean.isEmpty()) return;
        tts.speak(clean, TextToSpeech.QUEUE_ADD, null, "kurek_utt_" + System.currentTimeMillis());
    }

    private void stopAudio() {
        if (audioPlayer != null) {
            audioPlayer.stop();
        }
        if (tts != null) {
            tts.stop();
        }
    }

    private void setupSpeechRecognizer() {
        if (!SpeechRecognizer.isRecognitionAvailable(this)) return;

        speechRecognizer = SpeechRecognizer.createSpeechRecognizer(this);
        speechRecognizer.setRecognitionListener(new RecognitionListener() {
            @Override public void onReadyForSpeech(Bundle params) {}
            @Override public void onBeginningOfSpeech() {}
            @Override public void onRmsChanged(float rmsdB) {}
            @Override public void onBufferReceived(byte[] buffer) {}
            @Override public void onEndOfSpeech() { setListeningState(false); }
            @Override public void onError(int error) {
                setListeningState(false);
                if (error != SpeechRecognizer.ERROR_NO_MATCH && error != SpeechRecognizer.ERROR_SPEECH_TIMEOUT) {
                    Toast.makeText(MainActivity.this, "Voice error: " + error, Toast.LENGTH_SHORT).show();
                }
            }
            @Override
            public void onResults(Bundle results) {
                setListeningState(false);
                ArrayList<String> matches = results.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
                if (matches != null && !matches.isEmpty()) {
                    String recognized = matches.get(0);
                    dispatchUserPrompt(recognized);
                }
            }
            @Override public void onPartialResults(Bundle partialResults) {}
            @Override public void onEvent(int eventType, Bundle params) {}
        });
    }

    private void toggleVoiceRecording() {
        stopAudio();
        if (speechRecognizer == null) return;
        if (isListeningSpeech) {
            speechRecognizer.stopListening();
            setListeningState(false);
        } else {
            Intent intent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
            intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
            intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.getDefault());
            speechRecognizer.startListening(intent);
            setListeningState(true);
            triggerHaptic(60);
        }
    }

    private void setListeningState(boolean listening) {
        isListeningSpeech = listening;
        btnMic.setBackgroundResource(listening ? R.drawable.bg_mic_button_active : R.drawable.bg_mic_button);
    }

    private void dispatchUserPrompt(String prompt) {
        if (!isConfigured()) {
            Toast.makeText(this, "Set host and token in Settings first", Toast.LENGTH_LONG).show();
            showSettingsDialog();
            return;
        }

        stopAudio();
        triggerHaptic(40);
        ChatMessage userMsg = new ChatMessage(ChatMessage.TYPE_USER, prompt, getTime());
        messageList.add(userMsg);
        int userPos = messageList.size() - 1;
        chatAdapter.notifyItemInserted(userPos);

        ChatMessage kurekMsg = new ChatMessage(ChatMessage.TYPE_KUREK, "Thinking...", getTime());
        kurekMsg.setPending(true);
        messageList.add(kurekMsg);
        int kurekPos = messageList.size() - 1;
        chatAdapter.notifyItemInserted(kurekPos);
        recyclerChat.smoothScrollToPosition(kurekPos);

        setStatusState("THINKING", getColor(R.color.warn));

        final StringBuilder streamedContent = new StringBuilder();
        final int[] streamedSentencesCount = new int[]{0};
        final boolean[] receivedAudioChunk = new boolean[]{false};

        client.sendPrompt(host, port, token, prompt, speakOutLoud, new KurekClient.Callback() {
            @Override
            public void onToolCalled(String toolDescription) {
                triggerHaptic(20);
                kurekMsg.addToolCall(toolDescription);
                chatAdapter.notifyItemChanged(kurekPos);
            }

            @Override
            public void onAudio(byte[] audioBytes) {
                receivedAudioChunk[0] = true;
                if (speakOutLoud && audioPlayer != null) {
                    audioPlayer.enqueue(audioBytes);
                }
            }

            @Override
            public void onSentence(String sentence) {
                streamedSentencesCount[0]++;
                if (streamedContent.length() > 0) streamedContent.append(" ");
                streamedContent.append(sentence);
                kurekMsg.setText(streamedContent.toString());
                kurekMsg.setPending(false);
                chatAdapter.notifyItemChanged(kurekPos);
                recyclerChat.smoothScrollToPosition(kurekPos);

                // Fallback to local TTS only if server didn't stream xAI audio chunks
                if (speakOutLoud && !receivedAudioChunk[0]) {
                    speakText(sentence);
                }
            }

            @Override
            public void onDone(String fullReply) {
                triggerHaptic(50);
                String finalText = fullReply.isEmpty() ? (streamedContent.length() > 0 ? streamedContent.toString() : "Action completed.") : fullReply;
                kurekMsg.setText(finalText);
                kurekMsg.setPending(false);
                chatAdapter.notifyItemChanged(kurekPos);
                recyclerChat.smoothScrollToPosition(kurekPos);
                setStatusState("ONLINE", getColor(R.color.accent));

                if (speakOutLoud && streamedSentencesCount[0] == 0 && !receivedAudioChunk[0]) {
                    speakText(finalText);
                }
            }

            @Override
            public void onError(String error) {
                kurekMsg.setText("Error: " + error);
                kurekMsg.setPending(false);
                chatAdapter.notifyItemChanged(kurekPos);
                setStatusState("OFFLINE", getColor(R.color.error));
                if (speakOutLoud && !receivedAudioChunk[0]) {
                    speakText("Error: " + error);
                }
            }
        });
    }

    private void pingServer() {
        if (!isConfigured()) {
            setStatusState("SETUP", getColor(R.color.warn));
            return;
        }
        client.checkStatus(host, port, token, new KurekClient.StatusCallback() {
            @Override
            public void onStatus(String state) {
                setStatusState("ONLINE", getColor(R.color.accent));
            }

            @Override
            public void onError() {
                setStatusState("OFFLINE", getColor(R.color.error));
            }
        });
    }

    private void setStatusState(String text, int dotColor) {
        txtStatus.setText(text);
        statusDot.setBackgroundTintList(android.content.res.ColorStateList.valueOf(dotColor));
    }

    private void triggerHaptic(int ms) {
        if (vibrator != null) {
            vibrator.vibrate(android.os.VibrationEffect.createOneShot(ms, android.os.VibrationEffect.DEFAULT_AMPLITUDE));
        }
    }

    private void showSettingsDialog() {
        View dialogView = LayoutInflater.from(this).inflate(R.layout.dialog_settings, null);
        EditText eHost = dialogView.findViewById(R.id.editHost);
        EditText ePort = dialogView.findViewById(R.id.editPort);
        EditText eToken = dialogView.findViewById(R.id.editToken);
        SwitchCompat switchSpeak = dialogView.findViewById(R.id.switchSpeakOutLoud);
        TextView btnSave = dialogView.findViewById(R.id.btnSaveSettings);

        eHost.setText(host);
        ePort.setText(String.valueOf(port));
        eToken.setText(token);
        if (switchSpeak != null) {
            switchSpeak.setChecked(speakOutLoud);
        }

        AlertDialog dialog = new AlertDialog.Builder(this)
                .setView(dialogView)
                .create();

        if (dialog.getWindow() != null) {
            dialog.getWindow().setBackgroundDrawableResource(R.drawable.bg_dialog);
        }

        btnSave.setOnClickListener(v -> {
            String newHost = eHost.getText().toString().trim();
            int newPort = 8790;
            try { newPort = Integer.parseInt(ePort.getText().toString().trim()); } catch (Exception ignored) {}
            String newToken = eToken.getText().toString().trim();
            boolean newSpeak = switchSpeak != null && switchSpeak.isChecked();

            if (newHost.isEmpty() || newToken.isEmpty()) {
                Toast.makeText(this, "Host and token are required", Toast.LENGTH_LONG).show();
                return;
            }
            savePreferences(newHost, newPort, newToken, newSpeak);
            dialog.dismiss();
            Toast.makeText(this, "Settings saved", Toast.LENGTH_SHORT).show();
        });

        dialog.show();
    }

    private String getTime() {
        return new SimpleDateFormat("HH:mm", Locale.getDefault()).format(new Date());
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, @NonNull String[] permissions, @NonNull int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        boolean granted = grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED;
        if (requestCode == REQ_RECORD_AUDIO && granted) {
            toggleVoiceRecording();
        } else if (requestCode == REQ_LIVE_AUDIO
                && ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
            toggleLive();
        }
    }

    private void toggleLive() {
        if (LiveCallService.getState() != LiveCallService.State.IDLE) {
            startService(LiveCallService.stopIntent(this));
            return;
        }
        if (!isConfigured()) {
            Toast.makeText(this, "Set host and token in Settings first", Toast.LENGTH_LONG).show();
            showSettingsDialog();
            return;
        }
        boolean needMic = ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED;
        SharedPreferences sp = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        // Ask for notifications once; the call works without them, the End control just isn't shown.
        boolean needNotif = android.os.Build.VERSION.SDK_INT >= 33
                && !sp.getBoolean(KEY_ASKED_NOTIFICATIONS, false)
                && ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED;
        if (needNotif) {
            sp.edit().putBoolean(KEY_ASKED_NOTIFICATIONS, true).apply();
        }
        if (needMic || needNotif) {
            List<String> ask = new ArrayList<>();
            if (needMic) ask.add(Manifest.permission.RECORD_AUDIO);
            if (needNotif) ask.add(Manifest.permission.POST_NOTIFICATIONS);
            ActivityCompat.requestPermissions(this, ask.toArray(new String[0]), REQ_LIVE_AUDIO);
            return;
        }
        stopAudio();
        triggerHaptic(40);
        ContextCompat.startForegroundService(this, LiveCallService.startIntent(this));
    }

    private void applyLiveState(LiveCallService.State state) {
        switch (state) {
            case CONNECTING:
                chipLive.setText(R.string.live_connecting);
                setStatusState("CONNECTING", getColor(R.color.warn));
                break;
            case LIVE:
                chipLive.setText(R.string.live_end_chip);
                setStatusState("LIVE", getColor(R.color.accent));
                break;
            default:
                chipLive.setText(R.string.preset_live);
                break;
        }
    }

    private final LiveCallService.Listener liveListener = new LiveCallService.Listener() {
        @Override
        public void onStateChanged(LiveCallService.State state) {
            applyLiveState(state);
            if (state == LiveCallService.State.IDLE) pingServer();
        }

        @Override
        public void onUserText(String text) {
            appendLiveMessage(ChatMessage.TYPE_USER, text);
        }

        @Override
        public void onKurekText(String text) {
            appendLiveMessage(ChatMessage.TYPE_KUREK, text);
        }

        @Override
        public void onTool(String description) {
            pendingLiveTools.add(description);
        }

        @Override
        public void onError(String message) {
            Toast.makeText(MainActivity.this, message, Toast.LENGTH_LONG).show();
        }
    };

    @Override
    protected void onStart() {
        super.onStart();
        LiveCallService.addListener(liveListener);
        applyLiveState(LiveCallService.getState());
    }

    @Override
    protected void onStop() {
        LiveCallService.removeListener(liveListener);
        super.onStop();
    }

    // Tool calls arrive before Kurek's spoken answer; attach them to that answer.
    private final List<String> pendingLiveTools = new ArrayList<>();

    private void appendLiveMessage(int type, String text) {
        ChatMessage msg = new ChatMessage(type, text, getTime());
        if (type == ChatMessage.TYPE_KUREK) {
            for (String t : pendingLiveTools) msg.addToolCall(t);
            pendingLiveTools.clear();
        }
        messageList.add(msg);
        int pos = messageList.size() - 1;
        chatAdapter.notifyItemInserted(pos);
        recyclerChat.smoothScrollToPosition(pos);
    }

    @Override
    protected void onDestroy() {
        if (audioPlayer != null) {
            audioPlayer.stop();
        }
        if (speechRecognizer != null) {
            speechRecognizer.destroy();
        }
        if (tts != null) {
            tts.stop();
            tts.shutdown();
        }
        super.onDestroy();
    }
}
