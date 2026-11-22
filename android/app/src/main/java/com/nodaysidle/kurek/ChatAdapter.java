package com.nodaysidle.kurek;

import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;
import androidx.annotation.NonNull;
import androidx.recyclerview.widget.RecyclerView;
import java.util.List;

public class ChatAdapter extends RecyclerView.Adapter<RecyclerView.ViewHolder> {
    // The PC prefixes tool descriptions with a pictograph; the app shows its own vector icon instead.
    // Symbols (So, Sk), format and combining marks (variation selector, joiner) and spaces, by category.
    private static final String LEADING_PICTOGRAPHS = "^[\\p{So}\\p{Sk}\\p{Cf}\\p{M}\\s]+";

    static String stripLeadingPictographs(String description) {
        return description.replaceFirst(LEADING_PICTOGRAPHS, "");
    }

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
        LinearLayout layoutTools, bubble;
        ImageView iconError;

        KurekViewHolder(@NonNull View itemView) {
            super(superitemView(itemView));
            txtMessage = itemView.findViewById(R.id.txtKurekMessage);
            txtTime = itemView.findViewById(R.id.txtKurekTime);
            layoutTools = itemView.findViewById(R.id.layoutToolCalls);
            bubble = itemView.findViewById(R.id.bubbleKurek);
            iconError = itemView.findViewById(R.id.iconKurekError);
        }

        // "name(args)": the name on line 1, the arguments (without the parentheses) on line 2.
        private static void bindTool(View pill, String text) {
            TextView name = pill.findViewById(R.id.txtToolName);
            TextView args = pill.findViewById(R.id.txtToolDesc);
            int open = text.indexOf('(');
            if (open <= 0) {
                name.setText(text);
                args.setVisibility(View.GONE);
                return;
            }
            int close = text.lastIndexOf(')');
            String inner = (close > open ? text.substring(open + 1, close) : text.substring(open + 1)).trim();
            name.setText(text.substring(0, open).trim());
            args.setText(inner);
            args.setVisibility(inner.isEmpty() ? View.GONE : View.VISIBLE);
        }

        private static View superitemView(View v) { return v; }

        void bind(ChatMessage msg) {
            txtMessage.setText(msg.getText());
            txtTime.setText(msg.getTimestamp());

            // Errors carry an icon and a tinted bubble so they read without colour; pending is quieter.
            boolean isError = !msg.isPending() && msg.getText() != null && msg.getText().startsWith("Error: ");
            bubble.setBackgroundResource(isError ? R.drawable.bg_error_message : R.drawable.bg_kurek_message);
            iconError.setVisibility(isError ? View.VISIBLE : View.GONE);
            txtMessage.setTextColor(itemView.getContext().getColor(
                    msg.isPending() ? R.color.text_muted : R.color.text));

            layoutTools.removeAllViews();
            if (msg.getToolCalls().isEmpty()) {
                layoutTools.setVisibility(View.GONE);
            } else {
                layoutTools.setVisibility(View.VISIBLE);
                LayoutInflater inflater = LayoutInflater.from(itemView.getContext());
                for (String tool : msg.getToolCalls()) {
                    View pill = inflater.inflate(R.layout.item_tool_pill, layoutTools, false);
                    bindTool(pill, stripLeadingPictographs(tool));
                    layoutTools.addView(pill);
                }
            }
        }
    }
}
