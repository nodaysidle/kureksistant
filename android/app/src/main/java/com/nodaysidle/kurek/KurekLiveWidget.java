package com.nodaysidle.kurek;

import android.app.PendingIntent;
import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.widget.RemoteViews;

/** 2x1 home-screen widget: shows the live call state and toggles it through the trampoline. */
public class KurekLiveWidget extends AppWidgetProvider {
    @Override
    public void onUpdate(Context context, AppWidgetManager manager, int[] ids) {
        for (int id : ids) manager.updateAppWidget(id, build(context));
    }

    static void updateAll(Context context) {
        Context app = context.getApplicationContext();
        AppWidgetManager manager = AppWidgetManager.getInstance(app);
        int[] ids = manager.getAppWidgetIds(new ComponentName(app, KurekLiveWidget.class));
        if (ids.length == 0) return;
        RemoteViews views = build(app);
        for (int id : ids) manager.updateAppWidget(id, views);
    }

    private static RemoteViews build(Context context) {
        LiveCallService.State state = LiveCallService.getState();
        boolean idle = state == LiveCallService.State.IDLE;
        boolean live = state == LiveCallService.State.LIVE;
        RemoteViews v = new RemoteViews(context.getPackageName(), R.layout.widget_kurek_live);

        int statusText;
        switch (state) {
            case IDLE:
                statusText = R.string.widget_ready;
                break;
            case LIVE:
                statusText = R.string.widget_live;
                break;
            default:
                statusText = R.string.widget_connecting;
                break;
        }
        v.setTextViewText(R.id.widgetStatus, context.getString(statusText));
        v.setImageViewResource(R.id.widgetDot, state == LiveCallService.State.CONNECTING ? R.drawable.bg_dot_warn : R.drawable.bg_dot);

        v.setTextViewText(R.id.widgetButton, context.getString(idle ? R.string.preset_live : R.string.live_end));
        v.setInt(R.id.widgetButton, "setBackgroundResource", idle ? R.drawable.bg_widget_button_primary : R.drawable.bg_widget_button_outlined);
        v.setTextColor(R.id.widgetButton, context.getColor(idle ? R.color.surface_low : R.color.text));

        v.setViewVisibility(R.id.widgetTimer, live ? android.view.View.VISIBLE : android.view.View.GONE);
        v.setChronometer(R.id.widgetTimer, LiveCallService.getLiveBase(), null, live);

        v.setContentDescription(android.R.id.background, context.getString(idle ? R.string.widget_desc_talk : R.string.widget_desc_end));

        PendingIntent toggle = PendingIntent.getActivity(context, 0,
                new Intent(context, LiveCallTrampolineActivity.class),
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        v.setOnClickPendingIntent(android.R.id.background, toggle);
        v.setOnClickPendingIntent(R.id.widgetButton, toggle);
        return v;
    }
}
