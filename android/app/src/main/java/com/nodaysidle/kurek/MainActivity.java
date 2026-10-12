package com.nodaysidle.kurek;

import android.Manifest;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.os.Vibrator;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import android.view.LayoutInflater;
import android.view.View;
import android.view.inputmethod.EditorInfo;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ImageButton;
import android.widget.TextView;
import android.widget.Toast;
import androidx.annotation.NonNull;
import androidx.appcompat.app.AlertDialog;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;
import androidx.recyclerview.widget.LinearLayoutManager;
import androidx.recyclerview.widget.RecyclerView;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Locale;

public class MainActivity extends AppCompatActivity {
    private static final int REQ_RECORD_AUDIO = 1001;
    private static final String PREFS_NAME = "kurek_prefs";
    private static final String KEY_HOST = "kurek_host";
    private static final String KEY_PORT = "kurek_port";
    private static final String KEY_TOKEN = "kurek_token";

    private RecyclerView recyclerChat;
    private ChatAdapter chatAdapter;
    private List<ChatMessage> messageList;
    private EditText editPrompt;
    private ImageButton btnSend, btnMic, btnSettings;
    private TextView txtStatus, txtHostLabel;
    private View statusDot;
    private Vibrator vibrator;

    private SpeechRecognizer speechRecognizer;
    private boolean isListeningSpeech = false;

    private final KurekClient client = new KurekClient();
    private String host = "192.168.1.251";
    private int port = 8790;
    private String token = "kurek_sec_99a8b7c6d5e4f3a2";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        vibrator = (Vibrator) getSystemService(Context.VIBRATOR_SERVICE);
        loadPreferences();

        initViews();
        setupChatRecycler();
        setupListeners();
        setupSpeechRecognizer();

        pingServer();
    }

    private void loadPreferences() {
        SharedPreferences sp = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        host = sp.getString(KEY_HOST, "192.168.1.251");
        port = sp.getInt(KEY_PORT, 8790);
        token = sp.getString(KEY_TOKEN, "kurek_sec_99a8b7c6d5e4f3a2");
    }

    private void savePreferences(String h, int p, String t) {
        SharedPreferences sp = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        sp.edit().putString(KEY_HOST, h).putInt(KEY_PORT, p).putString(KEY_TOKEN, t).apply();
        this.host = h;
        this.port = p;
        this.token = t;
        txtHostLabel.setText("Workstation: " + host);
        pingServer();
    }

    private void initViews() {
        recyclerChat = findViewById(R.id.recyclerChat);
        editPrompt = findViewById(R.id.editPrompt);
        btnSend = findViewById(R.id.btnSend);
        btnMic = findViewById(R.id.btnMic);
        btnSettings = findViewById(R.id.btnSettings);
        txtStatus = findViewById(R.id.txtStatus);
        txtHostLabel = findViewById(R.id.txtHostLabel);
        statusDot = findViewById(R.id.statusDot);

        txtHostLabel.setText("Workstation: " + host);

        findViewById(R.id.chipGrokbot).setOnClickListener(v -> dispatchUserPrompt("Open Grokbot on my workstation"));
        findViewById(R.id.chipSysStatus).setOnClickListener(v -> dispatchUserPrompt("Check workstation system resources and CPU load"));
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

        // Add welcome message
        ChatMessage welcome = new ChatMessage(ChatMessage.TYPE_KUREK, "Kurek Remote connected. Speak or type instructions for your Arch workstation.", getTime());
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

        btnSettings.setOnClickListener(v -> showSettingsDialog());
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

        setStatusState("THINKING", getColor(R.color.status_amber));

        final StringBuilder streamedContent = new StringBuilder();

        client.sendPrompt(host, port, token, prompt, new KurekClient.Callback() {
            @Override
            public void onToolCalled(String toolDescription) {
                triggerHaptic(20);
                kurekMsg.addToolCall(toolDescription);
                chatAdapter.notifyItemChanged(kurekPos);
            }

            @Override
            public void onSentence(String sentence) {
                if (streamedContent.length() > 0) streamedContent.append(" ");
                streamedContent.append(sentence);
                kurekMsg.setText(streamedContent.toString());
                kurekMsg.setPending(false);
                chatAdapter.notifyItemChanged(kurekPos);
                recyclerChat.smoothScrollToPosition(kurekPos);
            }

            @Override
            public void onDone(String fullReply) {
                triggerHaptic(50);
                kurekMsg.setText(fullReply.isEmpty() ? (streamedContent.length() > 0 ? streamedContent.toString() : "Action completed.") : fullReply);
                kurekMsg.setPending(false);
                chatAdapter.notifyItemChanged(kurekPos);
                recyclerChat.smoothScrollToPosition(kurekPos);
                setStatusState("ONLINE", getColor(R.color.status_green));
            }

            @Override
            public void onError(String error) {
                kurekMsg.setText("❌ " + error);
                kurekMsg.setPending(false);
                chatAdapter.notifyItemChanged(kurekPos);
                setStatusState("OFFLINE", getColor(R.color.text_muted));
            }
        });
    }

    private void pingServer() {
        client.checkStatus(host, port, new KurekClient.StatusCallback() {
            @Override
            public void onStatus(String state) {
                setStatusState("ONLINE", getColor(R.color.status_green));
            }

            @Override
            public void onError() {
                setStatusState("OFFLINE", getColor(R.color.text_muted));
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
        Button btnSave = dialogView.findViewById(R.id.btnSaveSettings);

        eHost.setText(host);
        ePort.setText(String.valueOf(port));
        eToken.setText(token);

        AlertDialog dialog = new AlertDialog.Builder(this)
                .setView(dialogView)
                .create();

        btnSave.setOnClickListener(v -> {
            String newHost = eHost.getText().toString().trim();
            int newPort = 8790;
            try { newPort = Integer.parseInt(ePort.getText().toString().trim()); } catch (Exception ignored) {}
            String newToken = eToken.getText().toString().trim();

            if (!newHost.isEmpty()) {
                savePreferences(newHost, newPort, newToken);
                dialog.dismiss();
                Toast.makeText(this, "Settings saved!", Toast.LENGTH_SHORT).show();
            }
        });

        dialog.show();
    }

    private String getTime() {
        return new SimpleDateFormat("HH:mm", Locale.getDefault()).format(new Date());
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, @NonNull String[] permissions, @NonNull int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == REQ_RECORD_AUDIO && grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
            toggleVoiceRecording();
        }
    }

    @Override
    protected void onDestroy() {
        if (speechRecognizer != null) {
            speechRecognizer.destroy();
        }
        super.onDestroy();
    }
}
