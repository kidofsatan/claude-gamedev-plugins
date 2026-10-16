// Yandex Games store capture tools. Копируется плагином yandex-games в Assets/Editor/YandexStoreTools/.
// Все файлы пишутся в <проект>/yandex/raw/..., откуда их забирает ygtool.py.
//
// Меню "Yandex Games":
//   Auto Capture While Playing  — в Play Mode каждые N секунд скриншот в raw/screenshots/auto
//   Record Video While Playing  — в Play Mode пишет видео Game View в raw/video (нужен Unity Recorder)
//   Render Key Art              — рендер камеры "KeyArtCamera" (или Scene View) в raw/keyart: icon/cover/hero
// Горячие клавиши в Play Mode: F9 — скриншот, F10 — старт/стоп записи видео.
// CLI: Unity.exe -projectPath <p> -executeMethod YandexGames.StoreTools.CaptureSessionCli
//        -ygScene "Assets/Scenes/X.unity" -ygSeconds 90
using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
#if YG_RECORDER
using UnityEditor.Recorder;
using UnityEditor.Recorder.Encoder;
using UnityEditor.Recorder.Input;
#endif

namespace YandexGames
{
    [InitializeOnLoad]
    public static class StoreTools
    {
        const string AutoCaptureKey = "YG_AutoCapture";
        const string RecordKey = "YG_RecordVideo";
        const string IntervalKey = "YG_CaptureInterval";
        const string CliSecondsKey = "YG_CliSeconds";
        const string CliActiveKey = "YG_CliActive";

        public static string ProjectRoot => Directory.GetParent(Application.dataPath).FullName;
        public static string Raw(string sub)
        {
            var p = Path.Combine(ProjectRoot, "yandex", "raw", sub);
            Directory.CreateDirectory(p);
            return p;
        }

        public static float Interval => EditorPrefs.GetFloat(IntervalKey, 3f);

        static StoreTools()
        {
            EditorApplication.playModeStateChanged += OnPlayModeChanged;
        }

        static void OnPlayModeChanged(PlayModeStateChange s)
        {
            if (s == PlayModeStateChange.EnteredPlayMode)
            {
                var go = new GameObject("[YandexStoreCapture]") { hideFlags = HideFlags.HideAndDontSave };
                UnityEngine.Object.DontDestroyOnLoad(go);
                var c = go.AddComponent<StoreCaptureRunner>();
                c.autoCapture = EditorPrefs.GetBool(AutoCaptureKey, false);
                c.recordOnStart = EditorPrefs.GetBool(RecordKey, false);
                c.interval = Interval;
                c.quitAfter = EditorPrefs.GetBool(CliActiveKey, false) ? EditorPrefs.GetFloat(CliSecondsKey, 60f) : -1f;
            }
            else if (s == PlayModeStateChange.ExitingPlayMode)
            {
                VideoRecorder.Stop();
            }
            else if (s == PlayModeStateChange.EnteredEditMode && EditorPrefs.GetBool(CliActiveKey, false))
            {
                EditorPrefs.SetBool(CliActiveKey, false);
                EditorPrefs.SetBool(AutoCaptureKey, false);
                EditorPrefs.SetBool(RecordKey, false);
                EditorApplication.Exit(0);
            }
        }

        [MenuItem("Yandex Games/Auto Capture While Playing")]
        static void ToggleAuto() => EditorPrefs.SetBool(AutoCaptureKey, !EditorPrefs.GetBool(AutoCaptureKey, false));
        [MenuItem("Yandex Games/Auto Capture While Playing", true)]
        static bool ToggleAutoValidate()
        {
            Menu.SetChecked("Yandex Games/Auto Capture While Playing", EditorPrefs.GetBool(AutoCaptureKey, false));
            return true;
        }

        [MenuItem("Yandex Games/Record Video While Playing")]
        static void ToggleRecord()
        {
            bool on = !EditorPrefs.GetBool(RecordKey, false);
            EditorPrefs.SetBool(RecordKey, on);
            if (on)
                EditorUtility.DisplayDialog("Yandex Games",
                    "Запись видео включена для каждого запуска Play Mode.\n\nПока идёт запись, звука в редакторе не слышно " +
                    "(Unity Recorder забирает звук в файл). Выключите пункт после съёмки.", "OK");
        }
        [MenuItem("Yandex Games/Record Video While Playing", true)]
        static bool ToggleRecordValidate()
        {
            Menu.SetChecked("Yandex Games/Record Video While Playing", EditorPrefs.GetBool(RecordKey, false));
            return VideoRecorder.Available;
        }

        [MenuItem("Yandex Games/Open Output Folder")]
        static void OpenFolder() => EditorUtility.RevealInFinder(Path.Combine(ProjectRoot, "yandex", "raw"));

        const string HideUIKey = "YG_HideUI";
        public static bool HideUI => EditorPrefs.GetBool(HideUIKey, true);

        [MenuItem("Yandex Games/Hide UI in Captures")]
        static void ToggleHideUI() => EditorPrefs.SetBool(HideUIKey, !HideUI);
        [MenuItem("Yandex Games/Hide UI in Captures", true)]
        static bool ToggleHideUIValidate()
        {
            Menu.SetChecked("Yandex Games/Hide UI in Captures", HideUI);
            return true;
        }

        public static string CaptureScreenshot(string folder)
        {
            int w = Screen.width, h = Screen.height;
            int longSide = Mathf.Max(w, h);
            var sub = folder ?? (w >= h ? "screenshots/desktop" : "screenshots/mobile");
            var path = Path.Combine(Raw(sub), $"{(HideUI ? "clean_" : "")}{DateTime.Now:yyyyMMdd_HHmmss_fff}.png");
            if (HideUI)
            {
                // Кадр прямо с камер: экранный UI (Overlay) в него не попадает, на экране игрока он остаётся.
                float k = 1920f / Mathf.Max(1, longSide);
                int cw = Mathf.RoundToInt(w * k), ch = Mathf.RoundToInt(h * k);
                var rt = RenderTexture.GetTemporary(cw, ch, 24, RenderTextureFormat.ARGB32);
                CleanCapture.Render(rt);
                var prev = RenderTexture.active;
                RenderTexture.active = rt;
                var tex = new Texture2D(cw, ch, TextureFormat.RGB24, false);
                tex.ReadPixels(new Rect(0, 0, cw, ch), 0, 0);
                tex.Apply();
                RenderTexture.active = prev;
                RenderTexture.ReleaseTemporary(rt);
                File.WriteAllBytes(path, tex.EncodeToPNG());
                UnityEngine.Object.DestroyImmediate(tex);
                Debug.Log($"[Yandex] screenshot без UI {cw}x{ch} -> {path}");
                return path;
            }
            int super = Mathf.Clamp(Mathf.CeilToInt(1920f / Mathf.Max(1, longSide)), 1, 4);
            ScreenCapture.CaptureScreenshot(path, super);
            Debug.Log($"[Yandex] screenshot {w * super}x{h * super} -> {path}");
            return path;
        }

        // ------------------------------------------------------------ key art

        [MenuItem("Yandex Games/Render Key Art")]
        public static void RenderKeyArt()
        {
            var cam = FindKeyArtCamera(out bool temp);
            if (cam == null)
            {
                EditorUtility.DisplayDialog("Yandex Games", "Нет камеры KeyArtCamera и открытого Scene View.", "OK");
                return;
            }
            try
            {
                RenderCam(cam, 1024, 1024, "icon.png");
                RenderCam(cam, 1600, 940, "cover.png");
                RenderCam(cam, 3120, 1040, "hero.png");
            }
            finally
            {
                if (temp) UnityEngine.Object.DestroyImmediate(cam.gameObject);
            }
            EditorUtility.RevealInFinder(Raw("keyart"));
        }

        static Camera FindKeyArtCamera(out bool temp)
        {
            temp = false;
            var named = GameObject.Find("KeyArtCamera");
            if (named && named.TryGetComponent(out Camera c)) return c;
            var sv = SceneView.lastActiveSceneView;
            if (sv == null) return null;
            var go = new GameObject("[YG temp cam]") { hideFlags = HideFlags.HideAndDontSave };
            var cam = go.AddComponent<Camera>();
            cam.CopyFrom(sv.camera);
            var main = Camera.main;
            if (main)
            {
                cam.clearFlags = main.clearFlags;
                cam.backgroundColor = main.backgroundColor;
                cam.cullingMask = main.cullingMask;
            }
            temp = true;
            return cam;
        }

        static void RenderCam(Camera cam, int w, int h, string file)
        {
            var rt = RenderTexture.GetTemporary(w, h, 24, RenderTextureFormat.ARGB32);
            rt.antiAliasing = 8;
            var prevTarget = cam.targetTexture;
            var prevAspect = cam.aspect;
            cam.targetTexture = rt;
            cam.aspect = (float)w / h;
            cam.Render();
            var prevActive = RenderTexture.active;
            RenderTexture.active = rt;
            var tex = new Texture2D(w, h, TextureFormat.RGB24, false);
            tex.ReadPixels(new Rect(0, 0, w, h), 0, 0);
            tex.Apply();
            RenderTexture.active = prevActive;
            cam.targetTexture = prevTarget;
            cam.aspect = prevAspect;
            RenderTexture.ReleaseTemporary(rt);
            var path = Path.Combine(Raw("keyart"), file);
            File.WriteAllBytes(path, tex.EncodeToPNG());
            UnityEngine.Object.DestroyImmediate(tex);
            Debug.Log($"[Yandex] key art {w}x{h} -> {path}");
        }

        // ------------------------------------------------------------ build

        const long MaxUnpackedBytes = 100L * 1024 * 1024;

        [MenuItem("Yandex Games/Build WebGL + ZIP")]
        public static void BuildWebGLMenu()
        {
            var ok = BuildWebGL(out var message);
            EditorUtility.DisplayDialog("Yandex Games", message, "OK");
            if (ok) EditorUtility.RevealInFinder(Path.Combine(ProjectRoot, "yandex", "out", "build.zip"));
        }

        // Unity.exe -batchmode -quit -projectPath <p> -executeMethod YandexGames.StoreTools.BuildWebGLCli
        public static void BuildWebGLCli()
        {
            var ok = BuildWebGL(out var message);
            Debug.Log("[Yandex] " + message);
            EditorApplication.Exit(ok ? 0 : 1);
        }

        public static bool BuildWebGL(out string message)
        {
            if (EditorUserBuildSettings.activeBuildTarget != BuildTarget.WebGL &&
                !EditorUserBuildSettings.SwitchActiveBuildTarget(BuildTargetGroup.WebGL, BuildTarget.WebGL))
            {
                message = "Не удалось переключиться на платформу Web (установлен ли модуль WebGL Build Support?)";
                return false;
            }
            var scenes = System.Linq.Enumerable.ToArray(System.Linq.Enumerable.Select(
                System.Linq.Enumerable.Where(EditorBuildSettings.scenes, s => s.enabled), s => s.path));
            var outDir = Path.Combine(ProjectRoot, "yandex", "build", "webgl");
            if (Directory.Exists(outDir)) Directory.Delete(outDir, true);

            // BuildOptions.None — релизная сборка (Development Build выключен).
            var report = UnityEditor.BuildPipeline.BuildPlayer(new BuildPlayerOptions
            {
                scenes = scenes,
                locationPathName = outDir,
                target = BuildTarget.WebGL,
                options = BuildOptions.None
            });
            if (report.summary.result != UnityEditor.Build.Reporting.BuildResult.Succeeded)
            {
                message = $"Сборка не удалась: {report.summary.result}, ошибок: {report.summary.totalErrors}. См. Console.";
                return false;
            }

            long total = 0;
            var problems = new System.Collections.Generic.List<string>();
            foreach (var f in Directory.GetFiles(outDir, "*", SearchOption.AllDirectories))
            {
                total += new FileInfo(f).Length;
                var name = Path.GetFileName(f);
                if (name.Contains(" ") || System.Text.RegularExpressions.Regex.IsMatch(name, "[А-Яа-яЁё]"))
                    problems.Add("имя с пробелом/кириллицей: " + name);
            }
            if (!File.Exists(Path.Combine(outDir, "index.html"))) problems.Add("нет index.html в корне");
            if (total > MaxUnpackedBytes) problems.Add($"размер {total / 1048576f:F1} МБ > 100 МБ");

            WriteAssetReport(report, total);

            var zip = Path.Combine(ProjectRoot, "yandex", "out", "build.zip");
            Directory.CreateDirectory(Path.GetDirectoryName(zip));
            if (File.Exists(zip)) File.Delete(zip);
            System.IO.Compression.ZipFile.CreateFromDirectory(outDir, zip);

            message = $"Билд готов: {total / 1048576f:F1} МБ распаковано, архив {new FileInfo(zip).Length / 1048576f:F1} МБ\n{zip}";
            if (problems.Count > 0) message += "\n\nПРОБЛЕМЫ:\n- " + string.Join("\n- ", problems);
            return problems.Count == 0;
        }

        // yandex/out/build-assets.json — самые тяжёлые ассеты билда (вход для агента оптимизации веса).
        static void WriteAssetReport(UnityEditor.Build.Reporting.BuildReport report, long totalBytes)
        {
            var sizes = new System.Collections.Generic.Dictionary<string, (ulong size, string type)>();
            foreach (var pack in report.packedAssets)
            foreach (var info in pack.contents)
            {
                var path = string.IsNullOrEmpty(info.sourceAssetPath) ? "(built-in)" : info.sourceAssetPath;
                sizes.TryGetValue(path, out var prev);
                sizes[path] = (prev.size + info.packedSize, info.type != null ? info.type.Name : "?");
            }
            var sorted = System.Linq.Enumerable.ToList(System.Linq.Enumerable.OrderByDescending(sizes, kv => kv.Value.size));
            var sb = new System.Text.StringBuilder();
            sb.Append("{\n  \"unpackedBytes\": ").Append(totalBytes).Append(",\n  \"assets\": [\n");
            for (int i = 0; i < sorted.Count && i < 300; i++)
            {
                var kv = sorted[i];
                sb.Append("    {\"path\": \"").Append(kv.Key.Replace("\\", "/").Replace("\"", "\\\""))
                  .Append("\", \"type\": \"").Append(kv.Value.type)
                  .Append("\", \"bytes\": ").Append(kv.Value.size).Append(i + 1 < sorted.Count && i < 299 ? "},\n" : "}\n");
            }
            sb.Append("  ]\n}\n");
            File.WriteAllText(Path.Combine(ProjectRoot, "yandex", "out", "build-assets.json"), sb.ToString());
        }

        // ------------------------------------------------------------ CLI

        public static void CaptureSessionCli()
        {
            var args = Environment.GetCommandLineArgs();
            string scene = Arg(args, "-ygScene");
            float seconds = float.TryParse(Arg(args, "-ygSeconds"), System.Globalization.NumberStyles.Float,
                System.Globalization.CultureInfo.InvariantCulture, out var s) ? s : 60f;
            bool video = Arg(args, "-ygNoVideo") == null && VideoRecorder.Available;
            if (!string.IsNullOrEmpty(scene)) EditorSceneManager.OpenScene(scene);
            EditorPrefs.SetBool(AutoCaptureKey, true);
            EditorPrefs.SetBool(RecordKey, video);
            EditorPrefs.SetFloat(CliSecondsKey, seconds);
            EditorPrefs.SetBool(CliActiveKey, true);
            EditorApplication.EnterPlaymode();
        }

        static string Arg(string[] args, string name)
        {
            int i = Array.IndexOf(args, name);
            if (i < 0) return null;
            return i + 1 < args.Length ? args[i + 1] : "";
        }
    }

    public static class VideoRecorder
    {
#if YG_RECORDER
        static RecorderController controller;
        public static bool Available => true;
        public static bool IsRecording => controller != null && controller.IsRecording();

        public static void Start(int width = 1920, int height = 1080)
        {
            if (IsRecording) return;
            var cs = ScriptableObject.CreateInstance<RecorderControllerSettings>();
            var movie = ScriptableObject.CreateInstance<MovieRecorderSettings>();
            movie.name = "YandexGameplay";
            movie.Enabled = true;
            movie.EncoderSettings = new CoreEncoderSettings
            {
                Codec = CoreEncoderSettings.OutputCodec.MP4,
                EncodingQuality = CoreEncoderSettings.VideoEncodingQuality.High
            };
            if (StoreTools.HideUI)
            {
                // StoreCaptureRunner каждый кадр рендерит камеры в эту текстуру — видео без экранного UI.
                CleanCapture.VideoTarget = new RenderTexture(width, height, 24, RenderTextureFormat.ARGB32) { name = "YG clean capture" };
                CleanCapture.VideoTarget.Create();
                movie.ImageInputSettings = new RenderTextureInputSettings
                {
                    RenderTexture = CleanCapture.VideoTarget, OutputWidth = width, OutputHeight = height
                };
            }
            else movie.ImageInputSettings = new GameViewInputSettings { OutputWidth = width, OutputHeight = height };
            movie.AudioInputSettings.PreserveAudio = true;
            movie.OutputFile = Path.Combine(StoreTools.Raw("video"), $"gameplay_{(StoreTools.HideUI ? "clean_" : "")}{DateTime.Now:yyyyMMdd_HHmmss}");
            cs.AddRecorderSettings(movie);
            cs.SetRecordModeToManual();
            cs.FrameRate = 30;
            cs.CapFrameRate = true;
            controller = new RecorderController(cs);
            controller.PrepareRecording();
            controller.StartRecording();
            Debug.Log($"[Yandex] запись видео -> {movie.OutputFile}.mp4");
            // Recorder пишет звук через AudioRenderer — на время записи звук в колонках пропадает.
            Debug.LogWarning("[Yandex] Идёт запись видео со звуком: пока она включена, звук в редакторе НЕ слышен " +
                             "(особенность Unity Recorder). Выключить: Yandex Games → Record Video While Playing или F10.");
        }

        public static void Stop()
        {
            if (!IsRecording) return;
            controller.StopRecording();
            controller = null;
            if (CleanCapture.VideoTarget != null)
            {
                CleanCapture.VideoTarget.Release();
                UnityEngine.Object.DestroyImmediate(CleanCapture.VideoTarget);
                CleanCapture.VideoTarget = null;
            }
            Debug.Log("[Yandex] запись видео остановлена");
        }
#else
        public static bool Available => false;
        public static bool IsRecording => false;
        public static void Start(int width = 1920, int height = 1080) =>
            Debug.LogWarning("[Yandex] Unity Recorder не установлен (Package Manager: com.unity.recorder)");
        public static void Stop() { }
#endif
    }

    // Рендер кадра без экранного UI: все включённые камеры (кроме рендерящих в свои текстуры) по порядку depth,
    // Canvas в режиме Screen Space - Camera на время рендера выключаются. World Space UI остаётся (это часть сцены).
    public static class CleanCapture
    {
        public static RenderTexture VideoTarget;

        public static void Render(RenderTexture target)
        {
            var cams = Camera.allCameras.Where(c => c.enabled && c.targetTexture == null).OrderBy(c => c.depth).ToList();
            var hidden = UnityEngine.Object.FindObjectsByType<Canvas>(FindObjectsSortMode.None)
                .Where(c => c.enabled && c.isRootCanvas && c.renderMode == RenderMode.ScreenSpaceCamera).ToList();
            foreach (var c in hidden) c.enabled = false;
            try
            {
                foreach (var cam in cams)
                {
                    var prev = cam.targetTexture;
                    cam.targetTexture = target;
                    cam.Render();
                    cam.targetTexture = prev;
                }
            }
            finally
            {
                foreach (var c in hidden) c.enabled = true;
            }
        }
    }

    // Живёт только в Play Mode внутри редактора (создаётся из StoreTools).
    public class StoreCaptureRunner : MonoBehaviour
    {
        public bool autoCapture;
        public bool recordOnStart;
        public float interval = 3f;
        public float quitAfter = -1f;
        float nextShot;
        float started;

        void Start()
        {
            started = Time.realtimeSinceStartup;
            nextShot = started + Mathf.Max(2f, interval);
            if (recordOnStart) VideoRecorder.Start();
            StartCoroutine(CleanVideoLoop());
        }

        // Каждый кадр обновляет текстуру «чистого» видео, пока идёт запись с Hide UI.
        System.Collections.IEnumerator CleanVideoLoop()
        {
            var eof = new WaitForEndOfFrame();
            while (true)
            {
                yield return eof;
                if (CleanCapture.VideoTarget != null) CleanCapture.Render(CleanCapture.VideoTarget);
            }
        }

        void Update()
        {
#if ENABLE_LEGACY_INPUT_MANAGER
            if (Input.GetKeyDown(KeyCode.F9)) StoreTools.CaptureScreenshot(null);
            if (Input.GetKeyDown(KeyCode.F10))
            {
                if (VideoRecorder.IsRecording) VideoRecorder.Stop(); else VideoRecorder.Start();
            }
#elif ENABLE_INPUT_SYSTEM
            var kb = UnityEngine.InputSystem.Keyboard.current;
            if (kb != null && kb.f9Key.wasPressedThisFrame) StoreTools.CaptureScreenshot(null);
            if (kb != null && kb.f10Key.wasPressedThisFrame)
            {
                if (VideoRecorder.IsRecording) VideoRecorder.Stop(); else VideoRecorder.Start();
            }
#endif
            float now = Time.realtimeSinceStartup;
            if (autoCapture && now >= nextShot)
            {
                StoreTools.CaptureScreenshot("screenshots/auto");
                nextShot = now + interval;
            }
            if (quitAfter > 0 && now - started >= quitAfter)
            {
                quitAfter = -1f;
                VideoRecorder.Stop();
                EditorApplication.ExitPlaymode();
            }
        }
    }
}
