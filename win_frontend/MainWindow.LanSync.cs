using System;
using System.IO;
using System.Threading;
using System.Threading.Tasks;

namespace SuzuEmojy.Windows;

public partial class MainWindow
{
    private void OpenLanSync()
    {
        if (offscreen || LibraryBusy || commands is not { } runner) return;
        LanSyncDialog.Show(this, library, Export, Import, Sync);

        async Task Sync(Stream stream, Action<string> status, CancellationToken token)
        {
            try { await runner.Synchronize(stream, status, token).ConfigureAwait(false); }
            finally
            {
                // Windows may have committed even if the final phone receipt disconnected.
                var snapshot = await runner.ReadForLanSync().ConfigureAwait(false);
                await Dispatcher.InvokeAsync(() =>
                    AcceptSnapshot(snapshot, current?.Name, updateSettings: false, preserveSelection: true));
            }
        }

        async Task Export(string path, CancellationToken token)
        {
            var (result, _) = await runner.Run(new("export_zip", Value: path), false, token).ConfigureAwait(false);
            if (result.Error is { } error) throw new InvalidOperationException(error);
        }

        async Task<LanImportResult> Import(string path, CancellationToken token)
        {
            var (result, snapshot) = await runner.Run(new("import_zip", Value: path), true, token).ConfigureAwait(false);
            if (result.Error is { } error) throw new InvalidOperationException(error);
            await Dispatcher.InvokeAsync(() =>
            {
                if (snapshot is not null) AcceptSnapshot(snapshot, current?.Name, updateSettings: false, preserveSelection: true);
            });
            return new(result.Count, result.Skipped, result.Failed, result.Warnings?.Length ?? 0);
        }
    }
}
