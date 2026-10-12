package com.nodaysidle.kurek;

import java.util.ArrayList;
import java.util.List;

public class ChatMessage {
    public static final int TYPE_USER = 1;
    public static final int TYPE_KUREK = 2;

    private int type;
    private String text;
    private String timestamp;
    private List<String> toolCalls;
    private boolean isPending;

    public ChatMessage(int type, String text, String timestamp) {
        this.type = type;
        this.text = text;
        this.timestamp = timestamp;
        this.toolCalls = new ArrayList<>();
        this.isPending = false;
    }

    public int getType() { return type; }
    public String getText() { return text; }
    public void setText(String text) { this.text = text; }
    public String getTimestamp() { return timestamp; }
    public List<String> getToolCalls() { return toolCalls; }
    public void addToolCall(String toolCall) { this.toolCalls.add(toolCall); }
    public boolean isPending() { return isPending; }
    public void setPending(boolean pending) { isPending = pending; }
}
