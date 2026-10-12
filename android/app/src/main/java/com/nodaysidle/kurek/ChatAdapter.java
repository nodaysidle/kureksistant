package com.nodaysidle.kurek;

import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.TextView;
import androidx.annotation.NonNull;
import androidx.recyclerview.widget.RecyclerView;
import java.util.List;

public class ChatAdapter extends RecyclerView.Adapter<RecyclerView.ViewHolder> {
    private final List<ChatMessage> messages;

    public ChatAdapter(List<ChatMessage> messages) {
        this.messages = messages;
    }

    @Override
    public int getItemViewType(int position) {
        return messages.get(position).getType();
    }

    @NonNull
    @Override
    public RecyclerView.ViewHolder onCreateViewHolder(@NonNull ViewGroup parent, int viewType) {
        if (viewType == ChatMessage.TYPE_USER) {
            View view = LayoutInflater.from(parent.getContext()).inflate(R.layout.item_message_user, parent, false);
            return new UserViewHolder(view);
        } else {
            View view = LayoutInflater.from(parent.getContext()).inflate(R.layout.item_message_kurek, parent, false);
            return new KurekViewHolder(view);
        }
    }

    @Override
    public void onBindViewHolder(@NonNull RecyclerView.ViewHolder holder, int position) {
        ChatMessage msg = messages.get(position);
        if (holder instanceof UserViewHolder) {
            ((UserViewHolder) holder).bind(msg);
        } else if (holder instanceof KurekViewHolder) {
            ((KurekViewHolder) holder).bind(msg);
        }
    }

    @Override
    public int getItemCount() {
        return messages.size();
    }

    static class UserViewHolder extends RecyclerView.ViewHolder {
        TextView txtMessage, txtTime;

        UserViewHolder(@NonNull View itemView) {
            super(itemView);
            txtMessage = itemView.findViewById(R.id.txtUserMessage);
            txtTime = itemView.findViewById(R.id.txtUserTime);
        }

        void bind(ChatMessage msg) {
            txtMessage.setText(msg.getText());
            txtTime.setText(msg.getTimestamp());
        }
    }

    static class KurekViewHolder extends RecyclerView.ViewHolder {
        TextView txtMessage, txtTime;
        LinearLayout layoutTools;

        KurekViewHolder(@NonNull View itemView) {
            super(superitemView(itemView));
            txtMessage = itemView.findViewById(R.id.txtKurekMessage);
            txtTime = itemView.findViewById(R.id.txtKurekTime);
            layoutTools = itemView.findViewById(R.id.layoutToolCalls);
        }

        private static View superitemView(View v) { return v; }

        void bind(ChatMessage msg) {
            txtMessage.setText(msg.getText());
            txtTime.setText(msg.getTimestamp());

            layoutTools.removeAllViews();
            if (msg.getToolCalls().isEmpty()) {
                layoutTools.setVisibility(View.GONE);
            } else {
                layoutTools.setVisibility(View.VISIBLE);
                LayoutInflater inflater = LayoutInflater.from(itemView.getContext());
                for (String tool : msg.getToolCalls()) {
                    View pill = inflater.inflate(R.layout.item_tool_pill, layoutTools, false);
                    TextView desc = pill.findViewById(R.id.txtToolDesc);
                    desc.setText(tool);
                    layoutTools.addView(pill);
                }
            }
        }
    }
}
