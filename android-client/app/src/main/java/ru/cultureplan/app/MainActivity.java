package ru.cultureplan.app;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.provider.Settings;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import java.net.URI;
import java.net.URISyntaxException;
import java.util.Locale;

public class MainActivity extends Activity {
    private static final String PREFS = "culture_plan";
    private static final String SERVER_URL = "server_url";
    private static final int FILE_CHOOSER_REQUEST = 10;
    private static final int GREEN = Color.rgb(24, 61, 50);
    private static final int CREAM = Color.rgb(247, 242, 232);

    private SharedPreferences preferences;
    private WebView webView;
    private ProgressBar progressBar;
    private ValueCallback<Uri[]> fileChooserCallback;
    private String serverUrl;
    private String serverHost;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().setStatusBarColor(GREEN);
        getWindow().setNavigationBarColor(GREEN);
        preferences = getSharedPreferences(PREFS, MODE_PRIVATE);
        serverUrl = preferences.getString(SERVER_URL, "");
        if (serverUrl.isEmpty()) {
            showServerSetup();
        } else {
            showBrowser();
        }
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private TextView text(String value, int size, int color) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(size);
        view.setTextColor(color);
        return view;
    }

    private void showServerSetup() {
        if (webView != null) {
            webView.destroy();
            webView = null;
        }

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setGravity(Gravity.CENTER);
        root.setPadding(dp(28), dp(32), dp(28), dp(32));
        root.setBackgroundColor(CREAM);

        TextView mark = text("К", 34, Color.WHITE);
        mark.setGravity(Gravity.CENTER);
        mark.setBackgroundColor(GREEN);
        root.addView(mark, new LinearLayout.LayoutParams(dp(64), dp(64)));

        TextView title = text("Культурный план", 26, GREEN);
        title.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams titleParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        titleParams.setMargins(0, dp(22), 0, dp(8));
        root.addView(title, titleParams);

        TextView description = text(
                "Укажите адрес запущенного сервера. Телефон и компьютер должны видеть этот адрес по сети.",
                15, Color.DKGRAY);
        description.setGravity(Gravity.CENTER);
        description.setLineSpacing(0, 1.15f);
        root.addView(description, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        EditText input = new EditText(this);
        input.setSingleLine(true);
        input.setText(serverUrl.isEmpty() ? "http://10.0.2.2:8000" : serverUrl);
        input.setHint("https://calendar.example.ru");
        input.setInputType(android.text.InputType.TYPE_CLASS_TEXT
                | android.text.InputType.TYPE_TEXT_VARIATION_URI);
        LinearLayout.LayoutParams inputParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(56));
        inputParams.setMargins(0, dp(26), 0, dp(12));
        root.addView(input, inputParams);

        Button connect = new Button(this);
        connect.setText("Подключиться");
        connect.setTextColor(Color.WHITE);
        connect.setBackgroundColor(GREEN);
        connect.setOnClickListener(view -> {
            String normalized = normalizeServerUrl(input.getText().toString());
            if (normalized == null) {
                input.setError("Укажите полный адрес с http:// или https://");
                return;
            }
            serverUrl = normalized;
            preferences.edit().putString(SERVER_URL, serverUrl).apply();
            showBrowser();
        });
        root.addView(connect, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(52)));

        TextView hint = text(
                "Для эмулятора используйте 10.0.2.2 вместо 127.0.0.1. Для телефона укажите IP компьютера в локальной сети или публичный HTTPS-адрес.",
                12, Color.GRAY);
        hint.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams hintParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        hintParams.setMargins(0, dp(18), 0, 0);
        root.addView(hint, hintParams);
        setContentView(root);
    }

    private String normalizeServerUrl(String value) {
        String candidate = value.trim();
        while (candidate.endsWith("/")) {
            candidate = candidate.substring(0, candidate.length() - 1);
        }
        try {
            URI uri = new URI(candidate);
            String scheme = uri.getScheme();
            if (uri.getHost() == null || scheme == null
                    || !(scheme.equalsIgnoreCase("http") || scheme.equalsIgnoreCase("https"))) {
                return null;
            }
            return candidate;
        } catch (URISyntaxException error) {
            return null;
        }
    }

    private void showBrowser() {
        try {
            serverHost = new URI(serverUrl).getHost().toLowerCase(Locale.ROOT);
        } catch (URISyntaxException error) {
            showServerSetup();
            return;
        }

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.WHITE);

        LinearLayout toolbar = new LinearLayout(this);
        toolbar.setOrientation(LinearLayout.HORIZONTAL);
        toolbar.setGravity(Gravity.CENTER_VERTICAL);
        toolbar.setPadding(dp(16), 0, dp(8), 0);
        toolbar.setBackgroundColor(GREEN);

        TextView title = text("Культурный план", 19, Color.WHITE);
        title.setGravity(Gravity.CENTER_VERTICAL);
        toolbar.addView(title, new LinearLayout.LayoutParams(0, dp(52), 1));

        Button settingsButton = new Button(this);
        settingsButton.setText("Сервер");
        settingsButton.setTextColor(Color.WHITE);
        settingsButton.setBackgroundColor(Color.TRANSPARENT);
        settingsButton.setOnClickListener(view -> confirmServerChange());
        toolbar.addView(settingsButton, new LinearLayout.LayoutParams(dp(96), dp(48)));
        root.addView(toolbar);

        progressBar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progressBar.setMax(100);
        progressBar.setProgressTintList(android.content.res.ColorStateList.valueOf(Color.rgb(184, 92, 56)));
        root.addView(progressBar, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(3)));

        webView = new WebView(this);
        configureWebView(webView);
        root.addView(webView, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1));
        setContentView(root);
        webView.loadUrl(serverUrl);
    }

    @SuppressLint("SetJavaScriptEnabled")
    private void configureWebView(WebView view) {
        WebSettings settings = view.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(true);
        settings.setBuiltInZoomControls(false);
        settings.setDisplayZoomControls(false);
        settings.setMediaPlaybackRequiresUserGesture(true);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setUserAgentString(settings.getUserAgentString() + " CulturePlanAndroid/1.0");

        CookieManager cookies = CookieManager.getInstance();
        cookies.setAcceptCookie(true);
        cookies.setAcceptThirdPartyCookies(view, true);

        view.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView current, WebResourceRequest request) {
                return handleNavigation(request.getUrl());
            }

            @Override
            @SuppressWarnings("deprecation")
            public boolean shouldOverrideUrlLoading(WebView current, String url) {
                return handleNavigation(Uri.parse(url));
            }

            @Override
            public void onReceivedError(WebView current, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    Toast.makeText(MainActivity.this,
                            "Сервер недоступен. Проверьте адрес и подключение.", Toast.LENGTH_LONG).show();
                }
            }
        });

        view.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onProgressChanged(WebView current, int progress) {
                progressBar.setProgress(progress);
                progressBar.setVisibility(progress < 100 ? View.VISIBLE : View.GONE);
            }

            @Override
            public boolean onShowFileChooser(WebView current, ValueCallback<Uri[]> callback,
                                             FileChooserParams params) {
                if (fileChooserCallback != null) {
                    fileChooserCallback.onReceiveValue(null);
                }
                fileChooserCallback = callback;
                try {
                    Intent intent = params.createIntent();
                    intent.setType("image/*");
                    startActivityForResult(intent, FILE_CHOOSER_REQUEST);
                    return true;
                } catch (ActivityNotFoundException error) {
                    fileChooserCallback = null;
                    Toast.makeText(MainActivity.this, "Выбор файлов недоступен", Toast.LENGTH_LONG).show();
                    return false;
                }
            }
        });

        view.setDownloadListener((url, userAgent, contentDisposition, mimeType, contentLength) -> {
            if (url.startsWith("http://") || url.startsWith("https://")) {
                openExternal(Uri.parse(url));
            } else {
                Toast.makeText(this, "Этот файл можно скачать в браузерной версии", Toast.LENGTH_LONG).show();
            }
        });
    }

    private boolean handleNavigation(Uri uri) {
        String scheme = uri.getScheme();
        String host = uri.getHost();
        if (scheme != null && host != null
                && (scheme.equalsIgnoreCase("http") || scheme.equalsIgnoreCase("https"))) {
            String normalizedHost = host.toLowerCase(Locale.ROOT);
            if (normalizedHost.equals(serverHost) || isVkHost(normalizedHost)) {
                return false;
            }
        }
        openExternal(uri);
        return true;
    }

    private boolean isVkHost(String host) {
        return host.equals("vk.ru") || host.endsWith(".vk.ru")
                || host.equals("vk.com") || host.endsWith(".vk.com");
    }

    private void openExternal(Uri uri) {
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, uri));
        } catch (ActivityNotFoundException error) {
            Toast.makeText(this, "Нет приложения для открытия ссылки", Toast.LENGTH_LONG).show();
        }
    }

    private void confirmServerChange() {
        new AlertDialog.Builder(this)
                .setTitle("Изменить сервер?")
                .setMessage("Текущая веб-сессия будет закрыта на этом устройстве.")
                .setNegativeButton("Отмена", null)
                .setPositiveButton("Изменить", (dialog, which) -> {
                    CookieManager.getInstance().removeAllCookies(null);
                    CookieManager.getInstance().flush();
                    showServerSetup();
                })
                .show();
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != FILE_CHOOSER_REQUEST || fileChooserCallback == null) {
            return;
        }
        Uri[] result = WebChromeClient.FileChooserParams.parseResult(resultCode, data);
        fileChooserCallback.onReceiveValue(result);
        fileChooserCallback = null;
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onDestroy() {
        if (webView != null) {
            webView.destroy();
        }
        super.onDestroy();
    }
}
