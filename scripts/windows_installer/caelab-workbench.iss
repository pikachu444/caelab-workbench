; Rendered by build_windows_installer.py from one verified bundle snapshot.
[Setup]
AppId=CAELabWorkbench.User
AppName=CAELab Workbench
AppVersion=@@VERSION@@
DefaultDirName={localappdata}\Programs\CAELabWorkbench
DefaultGroupName=CAELab Workbench
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
UsePreviousAppDir=yes
ArchitecturesAllowed=x64
MinVersion=10.0
CloseApplications=no
RestartApplications=no
SetupMutex=CAELabWorkbench.User.Setup
UninstallDisplayIcon={app}\payloads\@@BUNDLE_ID@@\CAELabWorkbench.exe
OutputDir=compiler-output
OutputBaseFilename=caelab-workbench-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Dirs]
Name: "{localappdata}\CAELAB\Workbench\Workspace"; Flags: uninsneveruninstall

[Files]
@@FILES@@

[Icons]
Name: "{group}\CAELab Workbench"; Filename: "{app}\payloads\@@BUNDLE_ID@@\CAELabWorkbench.exe"; WorkingDir: "{localappdata}\CAELAB\Workbench\Workspace"
Name: "{group}\Uninstall CAELab Workbench"; Filename: "{uninstallexe}"

[Code]
function WorkbenchUseError(): String;
var
  Locator, Service, Processes: Variant;
begin
  Result := 'Unable to check running CAELab Workbench processes. Setup was stopped without closing applications.';
  try
    Locator := CreateOleObject('WbemScripting.SWbemLocator');
    Service := Locator.ConnectServer('', 'root\CIMV2');
    { No forward-only flag: Count must enumerate the result successfully. }
    Processes := Service.ExecQuery('SELECT Name FROM Win32_Process WHERE Name = ''CAELabWorkbench.exe''');
    if Processes.Count = 0 then
      Result := ''
    else
      Result := 'A CAELabWorkbench.exe process is running. Close all Workbench windows, portable tools and owned jobs normally, then retry.';
  except
    { Connection, query and Count failures all keep the blocking result. }
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := WorkbenchUseError();
end;

function InitializeUninstall(): Boolean;
var
  Reason: String;
begin
  Reason := WorkbenchUseError();
  Result := Reason = '';
  if not Result then
    SuppressibleMsgBox(Reason, mbError, MB_OK, IDOK);
end;
