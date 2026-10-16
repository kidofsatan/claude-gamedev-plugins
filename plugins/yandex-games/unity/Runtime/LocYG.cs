// LocYG — лёгкая локализация для Яндекс Игр без правки сцен. Копируется плагином yandex-games в Assets/LocYG/.
// Язык: LocYG.ForceLanguage(...) из кода (любой SDK: GamePush, свой мост) → PluginYG2 (модуль EnvirData:
// YG2.envir.language, если плагин есть — define PLUGIN_YG_2) → параметр ?lang= в адресе iframe Яндекс Игр →
// язык системы/браузера. Без PluginYG2 файл тоже компилируется. Словари — из
// Resources/LocYG/<lang>.json (генерирует `ygtool loc-build`). Любой текст, совпавший с русской
// строкой или шаблоном ("Место: {0}"), подменяется переводом — сцены и префабы не трогаются:
//  * TMP (TextMeshProUGUI / TextMeshPro) — по TMPro_EventManager.TEXT_CHANGED_EVENT. Событие приходит ИЗНУТРИ
//    перестроения меша (CanvasUpdateRegistry / TMP_UpdateManager), а там TMP игнорирует SetVerticesDirty:
//    новый text запишется, но меш останется старым (русским) до следующего изменения. Поэтому перевод
//    откладывается до Canvas.willRenderCanvases и применяется с ForceMeshUpdate().
//  * legacy UnityEngine.UI.Text и TextMesh — события нет; раз в PollFrames кадров проверяются активные
//    объекты, у которых текст изменился.
//  * на загрузке сцены и смене языка — все тексты, включая неактивные.
// В ru.json/<lang>.json есть и обратные пары «английский → язык»: если текст в сцене уже заменили
// на английский, русская версия всё равно покажет русский.
// Текст, который вводит игрок (textComponent у InputField / TMP_InputField), не переводится.
// В коде можно и явно: LocYG.T("Купить"), string.Format(LocYG.T("Место: {0}"), n).
// Непереведённая кириллица пишется в лог (редактор / development build): "[LocYG] нет перевода: ...".
using System;
using System.Collections.Generic;
using System.Text.RegularExpressions;
using TMPro;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.UI;
#if PLUGIN_YG_2
using YG;
#endif

public static class LocYG
{
    [Serializable] class Pair { public string ru; public string tr; }
    [Serializable] class Table { public Pair[] exact; public Pair[] patterns; }

    // Языки, для которых Яндекс показывает русскую версию.
    static readonly HashSet<string> RuGroup = new HashSet<string> { "ru", "be", "kk", "uk", "uz" };
    const int CacheLimit = 4096;
    const int PollFrames = 15;   // период проверки legacy Text/TextMesh, кадров

    public static string Lang { get; private set; } = "ru";
    public static event Action LanguageChanged;

    static Dictionary<string, string> _exact = new Dictionary<string, string>();
    static List<(Regex re, string tr)> _patterns = new List<(Regex, string)>();
    static readonly Dictionary<string, string> Cache = new Dictionary<string, string>();
    static readonly HashSet<TMP_Text> Pending = new HashSet<TMP_Text>();
    static readonly List<TMP_Text> PendingCopy = new List<TMP_Text>();
    static readonly Dictionary<int, string> LegacySeen = new Dictionary<int, string>();
    static readonly Dictionary<int, bool> InputText = new Dictionary<int, bool>();
    static bool _busy;
    static bool _loaded;
    static string _forced;
    static bool Empty => _exact.Count == 0 && _patterns.Count == 0;

    // Сброс статики, если в редакторе отключена перезагрузка домена (Enter Play Mode Options).
    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.SubsystemRegistration)]
    static void ResetStatics()
    {
        TMPro_EventManager.TEXT_CHANGED_EVENT.Remove(OnTextChanged);
        SceneManager.sceneLoaded -= OnSceneLoaded;
        Canvas.willRenderCanvases -= OnWillRenderCanvases;
        _loaded = false;
        _busy = false;
        _forced = null;
        Lang = "ru";
        _exact = new Dictionary<string, string>();
        _patterns = new List<(Regex, string)>();
        Cache.Clear();
        Reported.Clear();
        Pending.Clear();
        LegacySeen.Clear();
        InputText.Clear();
    }

    // AfterSceneLoad, а не BeforeSceneLoad: PluginYG2 в своём BeforeSceneLoad обнуляет YG2.onGetSDKData,
    // и ранняя подписка могла стереться (порядок одинаковых RuntimeInitialize не гарантирован).
    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    static void Init()
    {
        TMPro_EventManager.TEXT_CHANGED_EVENT.Add(OnTextChanged);
        SceneManager.sceneLoaded += OnSceneLoaded;
        Canvas.willRenderCanvases += OnWillRenderCanvases;
#if PLUGIN_YG_2
        YG2.onGetSDKData -= Apply;
        YG2.onGetSDKData += Apply;
#endif
        Apply();
    }

    static void OnSceneLoaded(Scene scene, LoadSceneMode mode)
    {
        LegacySeen.Clear();
        InputText.Clear();
        Apply();          // страховка: язык мог прийти, а событие — потеряться
        RetranslateAll();
    }

    public static void Apply() => SetLanguage(DetectLanguage());

    /// Для SDK без PluginYG2: передать язык, полученный от платформы ("ru", "en", "tr"...). null — снова автоопределение.
    public static void ForceLanguage(string lang)
    {
        _forced = string.IsNullOrEmpty(lang) ? null : lang;
        Apply();
    }

    public static string DetectLanguage()
    {
        string lang = _forced;
#if PLUGIN_YG_2 && EnvirData_yg
        if (string.IsNullOrEmpty(lang)) lang = YG2.envir.language;
#endif
        if (string.IsNullOrEmpty(lang)) lang = UrlLang();
        if (string.IsNullOrEmpty(lang))
            lang = Application.systemLanguage == SystemLanguage.Russian ? "ru" : "en";
        lang = lang.ToLowerInvariant();
        if (RuGroup.Contains(lang)) return "ru";
        return Resources.Load<TextAsset>("LocYG/" + lang) != null ? lang : "en";
    }

    // Яндекс Игры открывают игру в iframe с параметром ?lang=<код> (или &lang=).
    static string UrlLang()
    {
        var url = Application.absoluteURL;
        if (string.IsNullOrEmpty(url)) return null;
        var m = Regex.Match(url, @"[?&]lang=([a-zA-Z]{2,3})");
        return m.Success ? m.Groups[1].Value : null;
    }

    public static void SetLanguage(string lang)
    {
        if (lang == Lang && _loaded) return;
        _loaded = true;
        _exact = new Dictionary<string, string>();
        _patterns = new List<(Regex, string)>();
        Cache.Clear();
        // ru.json необязателен: правки русских текстов без открытия сцен (например, новое название игры).
        var asset = Resources.Load<TextAsset>("LocYG/" + lang);
        if (asset == null && lang != "ru")
            Debug.LogWarning($"[LocYG] нет Resources/LocYG/{lang}.json");
        if (asset != null)
        {
            var table = JsonUtility.FromJson<Table>(asset.text);
            foreach (var p in table.exact ?? Array.Empty<Pair>()) _exact[Norm(p.ru)] = p.tr;
            foreach (var p in table.patterns ?? Array.Empty<Pair>())
                _patterns.Add((new Regex("^" + Regex.Replace(Regex.Escape(Norm(p.ru)), @"\\\{\d+}", "(.*?)") + "$",
                    RegexOptions.Singleline), p.tr));
            _patterns.Sort((a, b) => b.re.ToString().Length.CompareTo(a.re.ToString().Length));
        }
        Lang = lang;
        Debug.Log($"[LocYG] язык: {lang}, строк: {_exact.Count}, шаблонов: {_patterns.Count}");
        LegacySeen.Clear();
        RetranslateAll();
        LanguageChanged?.Invoke();
    }

    /// Перевод строки на текущий язык; если перевода нет — исходная строка.
    public static string T(string ru)
    {
        if (Empty || string.IsNullOrEmpty(ru)) return ru;
        // Без кириллицы — только точные пары (обратные en→ru/tr для текстов, уже переведённых в сцене).
        if (!HasCyrillic(ru)) return _exact.TryGetValue(Norm(ru), out var direct) ? direct : ru;
        if (Cache.TryGetValue(ru, out var hit)) return hit;
        var key = Norm(ru);
        string res = ru;
        if (_exact.TryGetValue(key, out var tr)) res = tr;
        else
        {
            foreach (var (re, pattern) in _patterns)
            {
                var m = re.Match(key);
                if (!m.Success) continue;
                res = pattern;
                for (int i = 1; i < m.Groups.Count; i++)
                {
                    var arg = m.Groups[i].Value;
                    res = res.Replace("{" + (i - 1) + "}", _exact.TryGetValue(arg.Trim(), out var a) ? a : arg);
                }
                break;
            }
        }
        if (Cache.Count > CacheLimit) Cache.Clear();
        Cache[ru] = res;
        return res;
    }

    static string Norm(string s) => s.Replace("\r\n", "\n").Trim();

    // Нужно ли смотреть строку: русская (кириллица) или точный ключ словаря (обратные пары en→язык).
    static bool Candidate(string s) => !string.IsNullOrEmpty(s) && (HasCyrillic(s) || _exact.ContainsKey(Norm(s)));

    static bool HasCyrillic(string s)
    {
        for (int i = 0; i < s.Length; i++)
        {
            var c = s[i];
            if ((c >= 'А' && c <= 'я') || c == 'Ё' || c == 'ё') return true;
        }
        return false;
    }

    // ---------------------------------------------------------------- TMP

    static void OnTextChanged(UnityEngine.Object obj)
    {
        if (_busy || Empty || !(obj is TMP_Text t)) return;
        if (!Candidate(t.text)) return;
        // Мы внутри перестроения меша: присвоенный сейчас text не попадёт в меш. Откладываем.
        Pending.Add(t);
    }

    static void OnWillRenderCanvases()
    {
        if (Empty) return;
        if (Pending.Count > 0)
        {
            PendingCopy.Clear();
            PendingCopy.AddRange(Pending);
            Pending.Clear();
            foreach (var t in PendingCopy)
                if (t != null) Translate(t, true);
            PendingCopy.Clear();
        }
        if (Time.frameCount % PollFrames == 0) PollLegacy();
    }

    // Текст, который вводит игрок, не трогаем (placeholder поля — переводим).
    static bool IsUserInput(Component c)
    {
        var id = c.GetInstanceID();
        if (InputText.TryGetValue(id, out var r)) return r;
        var tmpField = c.GetComponentInParent<TMP_InputField>(true);
        var field = c.GetComponentInParent<InputField>(true);
        r = (tmpField != null && tmpField.textComponent == c) || (field != null && field.textComponent == c);
        InputText[id] = r;
        return r;
    }

    static void Translate(TMP_Text t, bool forceMesh)
    {
        var src = t.text;
        if (!Candidate(src) || IsUserInput(t)) return;
        var tr = T(src);
        if (ReferenceEquals(tr, src) || tr == src)
        {
            ReportMissing(src);
            return;
        }
        if (CanvasUpdateRegistry.IsRebuildingGraphics() || CanvasUpdateRegistry.IsRebuildingLayout())
        {
            Pending.Add(t);   // вызвано из перестроения — применим в willRenderCanvases
            return;
        }
        _busy = true;
        try
        {
            t.text = tr;
            if (forceMesh && t.isActiveAndEnabled) t.ForceMeshUpdate();
        }
        finally { _busy = false; }
    }

    // ---------------------------------------------------------------- legacy Text / TextMesh

    static void PollLegacy() => TranslateLegacyAll(FindObjectsInactive.Exclude);

    static void TranslateLegacyAll(FindObjectsInactive inactive)
    {
        foreach (var t in UnityEngine.Object.FindObjectsByType<UnityEngine.UI.Text>(inactive, FindObjectsSortMode.None))
        {
            var tr = LegacyTranslation(t, t.text);
            if (tr != null) t.text = tr;
        }
        foreach (var t in UnityEngine.Object.FindObjectsByType<TextMesh>(inactive, FindObjectsSortMode.None))
        {
            var tr = LegacyTranslation(t, t.text);
            if (tr != null) t.text = tr;
        }
    }

    // null — менять не нужно. Запоминает последнее увиденное значение, чтобы не переводить одно и то же.
    static string LegacyTranslation(Component c, string src)
    {
        var id = c.GetInstanceID();
        if (LegacySeen.TryGetValue(id, out var seen) && ReferenceEquals(seen, src)) return null;
        string result = null;
        if (Candidate(src) && !IsUserInput(c))
        {
            var tr = T(src);
            if (ReferenceEquals(tr, src) || tr == src) ReportMissing(src);
            else result = tr;
        }
        if (LegacySeen.Count > CacheLimit) LegacySeen.Clear();
        LegacySeen[id] = result ?? src;
        return result;
    }

    // ---------------------------------------------------------------- report

    static readonly HashSet<string> Reported = new HashSet<string>();

    // Пишет в лог русские тексты без перевода (редактор / development build), по одному разу.
    // `ygtool loc-missing` собирает их из Editor.log в yandex/loc/strings.json.
    static void ReportMissing(string src)
    {
        if (!Debug.isDebugBuild || Lang == "ru" || string.IsNullOrEmpty(src) || !HasCyrillic(src)) return;
        if (!Reported.Add(src)) return;
        Debug.LogWarning("[LocYG] нет перевода: " + src.Replace("\\", "\\\\").Replace("\r", "").Replace("\n", "\\n"));
    }

    /// Перевести все тексты сцены (и неактивные). Вызывается сам на загрузке сцены и смене языка.
    public static void RetranslateAll()
    {
        if (Empty) return;
        foreach (var t in UnityEngine.Object.FindObjectsByType<TMP_Text>(FindObjectsInactive.Include, FindObjectsSortMode.None))
            Translate(t, false);
        TranslateLegacyAll(FindObjectsInactive.Include);
    }
}
