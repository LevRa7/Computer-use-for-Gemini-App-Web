// =============================================================================
//  Antigravity Mesh - single-file Windows setup launcher
// =============================================================================
//
//  This is the compiled installer: it carries the whole node payload inside
//  itself (the WinForms wizard, install.ps1, core/, install.sh, skills/) as one
//  embedded zip, unpacks it under %LOCALAPPDATA%\AntigravityMesh\setup\<version>
//  and runs the wizard from there.
//
//  Why a launcher instead of a compiled wizard: the installation logic lives in
//  install.ps1, and the SSH variant needs core/ and install.sh next to the
//  wizard. Embedding them keeps a single downloadable file while leaving exactly
//  one implementation of the installation itself.
//
//  Deliberately written in C# 5 (the in-box csc.exe from .NET Framework 4.x) and
//  pure ASCII: csc reads a source file with the ANSI code page when there is no
//  BOM, so non-ASCII text here would break the build on a Russian Windows, the
//  same trap install-gui.ps1 avoids.
//
//  Build with build-installer-exe.ps1.
// =============================================================================

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Windows.Forms;

internal static class Launcher
{
    private const string ProductName = "Antigravity Mesh";
    private const string PayloadResource = "payload.zip";
    private const string EntryScript = "install-gui.ps1";
    private const int AttachParentProcess = -1;

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AttachConsole(int processId);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr GetStdHandle(int nStdHandle);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern uint GetFileType(IntPtr handle);

    private const int StdOutputHandle = -11;
    private const uint FileTypeDisk = 1;
    private const uint FileTypePipe = 3;

    [STAThread]
    private static int Main(string[] args)
    {
        Application.EnableVisualStyles();

        // Decide where output goes before touching any handle. When the caller
        // redirected stdout - a terminal pipe, a file - that handle is already the
        // right one, and AttachConsole would only disturb it; attaching is for the
        // double-click case, where a console may exist but not be ours.
        bool redirected = OutputIsCaptured();
        bool attached = false;
        if (!redirected)
        {
            try { attached = AttachConsole(AttachParentProcess); }
            catch { attached = false; }
            if (attached)
            {
                try
                {
                    UTF8Encoding utf8 = new UTF8Encoding(false);
                    Console.SetOut(new StreamWriter(Console.OpenStandardOutput(), utf8) { AutoFlush = true });
                    Console.SetError(new StreamWriter(Console.OpenStandardError(), utf8) { AutoFlush = true });
                }
                catch { attached = false; }
            }
        }

        // Output has somewhere to go in two of the three cases: a handle the
        // caller redirected, or a console we just attached to. Only with neither -
        // a plain double-click - is a message box the right place to show anything.
        bool canWrite = redirected || attached;

        if (args.Length == 1 && (args[0] == "--version" || args[0] == "-Version"))
        {
            return Report(Version() + Environment.NewLine, canWrite, 0);
        }
        if (args.Length == 1 && (args[0] == "--help" || args[0] == "-Help" || args[0] == "/?"))
        {
            return Report(Usage(), canWrite, 0);
        }
        if (args.Length == 1 && args[0] == "--where")
        {
            return Report(WhereReport(), canWrite, 0);
        }
        if (args.Length == 1 && args[0] == "--unpack")
        {
            // Unpack and stop. Pre-stages the payload, and it is how the refresh
            // behaviour is verified without performing an installation.
            try
            {
                string unpacked = EnsurePayload();
                return Report("unpacked " + Version() + " to " + unpacked + Environment.NewLine,
                              canWrite, 0);
            }
            catch (Exception ex)
            {
                return Fail("Could not unpack the installer payload.", ex, canWrite);
            }
        }

        string root;
        try
        {
            root = EnsurePayload();
        }
        catch (Exception ex)
        {
            return Fail("Could not unpack the installer payload."
                      + Environment.NewLine + Environment.NewLine
                      + "Set MESH_SETUP_DIR to unpack somewhere else, for example:"
                      + Environment.NewLine
                      + "  set MESH_SETUP_DIR=%USERPROFILE%\\AntigravityMesh", ex, canWrite);
        }

        string shell = ResolvePowerShell();
        if (shell == null)
        {
            return Fail("Windows PowerShell was not found. It is part of Windows; "
                      + "the installer cannot run without it.", null, canWrite);
        }

        ProcessStartInfo startInfo = new ProcessStartInfo();
        startInfo.FileName = shell;
        startInfo.Arguments = BuildArguments(Path.Combine(root, EntryScript), args);
        startInfo.WorkingDirectory = root;
        startInfo.UseShellExecute = false;
        startInfo.CreateNoWindow = true;
        startInfo.RedirectStandardOutput = true;
        startInfo.RedirectStandardError = true;
        startInfo.StandardOutputEncoding = new UTF8Encoding(false);
        startInfo.StandardErrorEncoding = new UTF8Encoding(false);

        StringBuilder captured = new StringBuilder();
        Process child;
        try
        {
            child = Process.Start(startInfo);
        }
        catch (Exception ex)
        {
            return Fail("Could not start the installer.", ex, canWrite);
        }

        // Read asynchronously: waiting on a full pipe would deadlock, and the
        // self-test prints a few kilobytes of JSON.
        child.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e)
        {
            if (e.Data == null) { return; }
            captured.AppendLine(e.Data);
            if (canWrite) { Console.Out.WriteLine(e.Data); }
        };
        child.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e)
        {
            if (e.Data == null) { return; }
            captured.AppendLine(e.Data);
            if (canWrite) { Console.Error.WriteLine(e.Data); }
        };
        child.BeginOutputReadLine();
        child.BeginErrorReadLine();
        child.WaitForExit();

        int code = child.ExitCode;
        child.Dispose();

        // A GUI user has no console, so a failure would otherwise be silent.
        if (!canWrite && code != 0)
        {
            string text = captured.ToString().Trim();
            if (text.Length == 0) { text = "The installer exited with code " + code + "."; }
            MessageBox.Show(text, ProductName, MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
        return code;
    }

    private static string Version()
    {
        System.Version version = Assembly.GetExecutingAssembly().GetName().Version;
        return string.Format("{0}.{1}.{2}", version.Major, version.Minor, version.Build);
    }

    private static string Usage()
    {
        return ProductName + " setup " + Version() + Environment.NewLine
             + Environment.NewLine
             + "  AntigravityMesh-Setup.exe                 open the visual installer" + Environment.NewLine
             + "  AntigravityMesh-Setup.exe -Lang ru        start in Russian" + Environment.NewLine
             + "  AntigravityMesh-Setup.exe -Lang en        start in English" + Environment.NewLine
             + "  AntigravityMesh-Setup.exe -SelfTest       headless self-check, prints JSON" + Environment.NewLine
             + "  AntigravityMesh-Setup.exe --version       print the version" + Environment.NewLine
             + "  AntigravityMesh-Setup.exe --where         show where the payload goes" + Environment.NewLine
             + "  AntigravityMesh-Setup.exe --unpack        unpack the payload and stop" + Environment.NewLine
             + Environment.NewLine
             + "Everything is unpacked under" + Environment.NewLine
             + "  %LOCALAPPDATA%\\AntigravityMesh\\setup\\<version>" + Environment.NewLine
             + "  (override with MESH_SETUP_DIR; a location that cannot be written" + Environment.NewLine
             + "   falls back to %TEMP%, then to the folder holding this file)" + Environment.NewLine;
    }

    // --where: which location the payload will use, and why each earlier candidate
    // was rejected. Written for support: "it unpacked somewhere unexpected" is the
    // question this answers.
    private static string WhereReport()
    {
        StringBuilder report = new StringBuilder();
        string overridden = Environment.GetEnvironmentVariable("MESH_SETUP_DIR");
        report.AppendLine("MESH_SETUP_DIR = " + (overridden == null ? "(not set)" : overridden));
        report.AppendLine("LOCALAPPDATA   = " + Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData));
        report.AppendLine("TEMP           = " + Path.GetTempPath());
        report.AppendLine("current dir    = " + Environment.CurrentDirectory);
        report.AppendLine();

        string[] candidates = CandidateRoots();
        for (int i = 0; i < candidates.Length; i++)
        {
            string reason;
            bool writable = IsWritable(candidates[i], out reason);
            report.AppendLine((writable ? "writable   " : "NOT usable ") + candidates[i]);
            if (!writable) { report.AppendLine("           " + reason); }
        }
        report.AppendLine();
        report.AppendLine("chosen         = " + PayloadRoot());
        return report.ToString();
    }

    // The directories the payload may be unpacked into, in order of preference.
    private static string[] CandidateRoots()
    {
        List<string> roots = new List<string>();
        roots.Add(Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "AntigravityMesh", "setup", Version()));
        roots.Add(Path.Combine(
            Path.GetTempPath(), "AntigravityMesh", "setup", Version()));
        // Last resort: beside the executable, for a machine where both are closed.
        string directory = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
        if (string.IsNullOrEmpty(directory)) { directory = Environment.CurrentDirectory; }
        roots.Add(Path.Combine(directory, "AntigravityMesh-setup-" + Version()));
        return roots.ToArray();
    }

    // The directory the payload is unpacked into. Each candidate is probed with a
    // real write before it is chosen: "Directory.CreateDirectory on an existing
    // directory succeeds even without write access", so a location can look usable
    // and still fail on the first file the installer tries to create.
    private static string PayloadRoot()    {
        // MESH_SETUP_DIR lets an operator unpack somewhere else - a locked-down
        // machine, another drive - and it is also what makes the executable
        // testable without touching the user profile.
        string overridden = Environment.GetEnvironmentVariable("MESH_SETUP_DIR");
        if (overridden != null && overridden.Trim().Length > 0)
        {
            return overridden.Trim();
        }

        string[] candidates = CandidateRoots();
        for (int i = 0; i < candidates.Length; i++)
        {
            if (IsWritable(candidates[i])) { return candidates[i]; }
        }
        return candidates[candidates.Length - 1];
    }

    private static bool IsWritable(string directory)
    {
        string reason;
        return IsWritable(directory, out reason);
    }

    private static bool IsWritable(string directory, out string reason)
    {
        reason = "";
        string probe = null;
        try
        {
            Directory.CreateDirectory(directory);
            probe = Path.Combine(directory, "write-probe-" + Guid.NewGuid().ToString("N") + ".tmp");
            using (FileStream stream = File.Create(probe)) { stream.WriteByte(0); }
            File.Delete(probe);
            return true;
        }
        catch (Exception ex)
        {
            reason = ex.GetType().Name + ": " + ex.Message;
            if (probe != null)
            {
                try { if (File.Exists(probe)) { File.Delete(probe); } }
                catch { }
            }
            return false;
        }
    }

    // Unpack the embedded zip, then return the directory the wizard runs from.
    // Extracting over an existing directory is deliberate: it repairs an
    // interrupted first run without deleting a node that is already running.
    private static string EnsurePayload()
    {
        string root = PayloadRoot();
        string marker = Path.Combine(root, ".payload-ok");

        // The marker records the hash of the payload this executable carries, not
        // the version. Keying it on the version meant a rebuilt executable reused
        // whatever the first build had unpacked and quietly ran a stale wizard.
        string stamp = PayloadInfo.Sha256;
        string unpacked = null;
        try
        {
            if (File.Exists(marker)) { unpacked = File.ReadAllText(marker).Trim(); }
        }
        catch { unpacked = null; }
        if (unpacked == stamp && File.Exists(Path.Combine(root, EntryScript)))
        {
            return root;
        }

        Directory.CreateDirectory(root);
        // The scratch copy of the payload lives beside its destination rather than
        // in %TEMP%: the directory is already ours, and an installer should not
        // depend on a temp path being writable.
        string temporary = Path.Combine(
            root, "payload-" + Guid.NewGuid().ToString("N") + ".tmp");
        try
        {
            using (Stream source = Assembly.GetExecutingAssembly().GetManifestResourceStream(PayloadResource))
            {
                if (source == null)
                {
                    throw new InvalidOperationException(
                        "The embedded payload is missing from this executable.");
                }
                using (FileStream target = File.Create(temporary)) { source.CopyTo(target); }
            }

            using (ZipArchive archive = ZipFile.OpenRead(temporary))
            {
                foreach (ZipArchiveEntry entry in archive.Entries)
                {
                    if (string.IsNullOrEmpty(entry.Name)) { continue; }   // directory entry
                    string relative = entry.FullName.Replace('/', Path.DirectorySeparatorChar);
                    string destination = Path.Combine(root, relative);
                    string directory = Path.GetDirectoryName(destination);
                    if (!string.IsNullOrEmpty(directory)) { Directory.CreateDirectory(directory); }
                    entry.ExtractToFile(destination, true);
                }
            }
            File.WriteAllText(marker, stamp);
        }
        finally
        {
            try { if (File.Exists(temporary)) { File.Delete(temporary); } }
            catch { }
        }
        return root;
    }

    private static string ResolvePowerShell()
    {
        string windows = Environment.GetFolderPath(Environment.SpecialFolder.Windows);
        string candidate = Path.Combine(
            windows, @"System32\WindowsPowerShell\v1.0\powershell.exe");
        if (File.Exists(candidate)) { return candidate; }
        return null;
    }

    // True when our own stdout is a pipe or a file, i.e. the caller redirected it.
    // A window application started by a double-click has no valid handle at all,
    // and that is the one case that has to fall back to a message box.
    private static bool OutputIsCaptured()
    {
        try
        {
            IntPtr handle = GetStdHandle(StdOutputHandle);
            if (handle == IntPtr.Zero || handle == new IntPtr(-1)) { return false; }
            uint type = GetFileType(handle);
            return type == FileTypeDisk || type == FileTypePipe;
        }
        catch
        {
            return false;
        }
    }

    private static string BuildArguments(string script, string[] args)
    {
        StringBuilder builder = new StringBuilder();
        builder.Append("-NoProfile -ExecutionPolicy Bypass -Sta -File ");
        builder.Append(Quote(script));
        for (int i = 0; i < args.Length; i++)
        {
            builder.Append(' ');
            builder.Append(Quote(args[i]));
        }
        return builder.ToString();
    }

    private static string Quote(string value)
    {
        if (value.Length > 0 && value.IndexOfAny(new char[] { ' ', '\t', '"' }) < 0)
        {
            return value;
        }
        return "\"" + value.Replace("\"", "\\\"") + "\"";
    }

    private static int Report(string text, bool canWrite, int code)
    {
        if (canWrite)
        {
            Console.Out.Write(text);
        }
        else
        {
            MessageBox.Show(text, ProductName, MessageBoxButtons.OK, MessageBoxIcon.Information);
        }
        return code;
    }

    private static int Fail(string message, Exception exception, bool canWrite)
    {
        string text = message;
        if (exception != null)
        {
            text += Environment.NewLine + Environment.NewLine + exception.Message;
        }
        if (canWrite)
        {
            Console.Error.WriteLine(text);
        }
        else
        {
            MessageBox.Show(text, ProductName, MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
        return 1;
    }
}
