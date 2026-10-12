package com.nodaysidle.kurek;

import android.os.Handler;
import android.os.Looper;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

public class KurekClient {
    public interface Callback {
        void onToolCalled(String toolDescription);
        void onSentence(String sentence);
        void onDone(String fullReply);
        void onError(String error);
    }

    public interface StatusCallback {
        void onStatus(String state);
        void onError();
    }

    private final Handler mainHandler = new Handler(Looper.getMainLooper());

    public void checkStatus(String host, int port, StatusCallback cb) {
        new Thread(() -> {
            try {
                URL url = new URL("http://" + host + ":" + port + "/status");
                HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                conn.setConnectTimeout(2500);
                conn.setReadTimeout(2500);
                conn.setRequestMethod("GET");
                if (conn.getResponseCode() == 200) {
                    BufferedReader reader = new BufferedReader(new InputStreamReader(conn.getInputStream()));
                    StringBuilder sb = new StringBuilder();
                    String line;
                    while ((line = reader.readLine()) != null) sb.append(line);
                    reader.close();
                    JSONObject json = new JSONObject(sb.toString());
                    String state = json.optString("state", "ONLINE");
                    mainHandler.post(() -> cb.onStatus(state));
                } else {
                    mainHandler.post(cb::onError);
                }
            } catch (Exception e) {
                mainHandler.post(cb::onError);
            }
        }).start();
    }

    public void sendPrompt(String host, int port, String token, String prompt, Callback cb) {
        new Thread(() -> {
            HttpURLConnection conn = null;
            try {
                URL url = new URL("http://" + host + ":" + port + "/remote/prompt");
                conn = (HttpURLConnection) url.openConnection();
                conn.setRequestMethod("POST");
                conn.setConnectTimeout(6000);
                conn.setReadTimeout(90000); // Allow time for multi-tool execution & LLM reasoning
                conn.setRequestProperty("Content-Type", "application/json");
                if (token != null && !token.trim().isEmpty()) {
                    conn.setRequestProperty("Authorization", "Bearer " + token.trim());
                }
                conn.setDoOutput(true);

                JSONObject payload = new JSONObject();
                payload.put("prompt", prompt);
                payload.put("remote", true);

                OutputStream os = conn.getOutputStream();
                os.write(payload.toString().getBytes(StandardCharsets.UTF_8));
                os.flush();
                os.close();

                int code = conn.getResponseCode();
                if (code != 200) {
                    InputStream errStream = conn.getErrorStream();
                    String err = "HTTP " + code;
                    if (errStream != null) {
                        BufferedReader r = new BufferedReader(new InputStreamReader(errStream));
                        err = r.readLine();
                        r.close();
                    }
                    final String finalErr = err;
                    mainHandler.post(() -> cb.onError("Server error: " + finalErr));
                    return;
                }

                BufferedReader reader = new BufferedReader(new InputStreamReader(conn.getInputStream(), StandardCharsets.UTF_8));
                String line;
                while ((line = reader.readLine()) != null) {
                    line = line.trim();
                    if (line.isEmpty() || !line.startsWith("data:")) continue;
                    String jsonStr = line.substring(5).trim();
                    if (jsonStr.isEmpty()) continue;

                    JSONObject event = new JSONObject(jsonStr);
                    String type = event.optString("type");

                    if ("tool".equals(type)) {
                        String desc = event.optString("description", "⚙️ Executing action...");
                        mainHandler.post(() -> cb.onToolCalled(desc));
                    } else if ("sentence".equals(type)) {
                        String sentence = event.optString("sentence", "");
                        mainHandler.post(() -> cb.onSentence(sentence));
                    } else if ("done".equals(type)) {
                        String fullReply = event.optString("reply", "");
                        mainHandler.post(() -> cb.onDone(fullReply));
                        break;
                    } else if ("error".equals(type)) {
                        String error = event.optString("error", "Unknown error");
                        mainHandler.post(() -> cb.onError(error));
                        break;
                    }
                }
                reader.close();
            } catch (Exception e) {
                mainHandler.post(() -> cb.onError("Connection failed: " + e.getMessage()));
            } finally {
                if (conn != null) conn.disconnect();
            }
        }).start();
    }
}
