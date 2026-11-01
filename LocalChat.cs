using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

static class Program
{
    [STAThread]
    static void Main()
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string script = Path.Combine(root, "open-chat.ps1");
        string powershell = Path.Combine(
            Environment.SystemDirectory,
            @"WindowsPowerShell\v1.0\powershell.exe");

        if (!File.Exists(script))
        {
            MessageBox.Show(
                "Keep Local Chat.exe in the LLM folder, next to open-chat.ps1.",
                "Local chat",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
            return;
        }

        ProcessStartInfo psi = new ProcessStartInfo();
        psi.FileName = powershell;
        psi.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"" + script + "\"";
        psi.WorkingDirectory = root;
        psi.UseShellExecute = false;
        psi.CreateNoWindow = true;
        psi.RedirectStandardOutput = true;
        psi.RedirectStandardError = true;

        try
        {
            Process process = Process.Start(psi);
            string stdout = process.StandardOutput.ReadToEnd();
            string stderr = process.StandardError.ReadToEnd();
            process.WaitForExit();
            if (process.ExitCode != 0)
            {
                string detail = (stderr + Environment.NewLine + stdout).Trim();
                if (detail.Length == 0)
                {
                    detail = "Startup exited with code " + process.ExitCode + ".";
                }
                MessageBox.Show(detail, "Local chat", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }
        catch (Exception ex)
        {
            MessageBox.Show(ex.Message, "Local chat", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}
