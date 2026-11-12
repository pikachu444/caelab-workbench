# Reconstructed B reference bundle

B-RECOVERY-1 is a new reconstruction after the execution environment became
unavailable. It is not byte-identical restoration of the former B bundle. The
historical observations JSON transcribes already-visible numerical results; it
does not invent original receipts, CSV bytes, source binary provenance or a new
successful execution. These reconstructed scripts still require a fresh run and
independent review. Source-only asset hashes identify these new reviewed bytes.

Use an explicit optional Python 3.12 environment. Previously tested numerical
versions were NumPy 2.3.5, SciPy 1.17.0, pyvisco 2.1.3, filelock 4.2.10. pyvisco's
declared dependencies are installed by pip. No package/runtime is vendored here.

~~~powershell
py -3.12 -m venv C:\CAE\b-reference-env
C:\CAE\b-reference-env\Scripts\python.exe -m pip install numpy==2.3.5 scipy==1.17.0 pyvisco==2.1.3 filelock==4.2.10
$Py = 'C:\CAE\b-reference-env\Scripts\python.exe'
$B = 'C:\CAE\workbench\docs\specs\cae-research\evidence\B'
$WB = 'C:\CAE\workbench'
& $Py "$B\reference_probe.py"
& $Py "$B\equilibrium_probe.py"
& $Py "$B\run_pyvisco.py"
& $Py "$B\optimizer_probe.py" --workbench-root $WB
& $Py "$B\constraint_probe.py" --workbench-root $WB
& $Py "$B\check_assets.py"
& $Py "$B\run_neml.py"
~~~

On Linux use the optional environment's bin/python and ordinary POSIX paths. All
paths above are user-selected examples. Scripts derive their own data bundle
location from __file__, not their cwd or a guessed parent/workbench directory.
The explicit core root must match the seven critical source SHA256 values in
workbench_core_sources.json. They belong to baseline
104cdd6888a5554eecc8cf965c60ff1bd980b6da; a docs-only HEAD may differ. The helper
checks bytes before import and actual loaded origins/hashes afterward. It does
not authenticate every optional dependency from seven core files. Actual loaded
pyvisco evaluator files are separately checked against source_manifest.json.

reference_probe generates new synthetic observations and receipts. Its updated
COMP fixture uses the same finite-domain unique-root algorithm as the dedicated
equilibrium probe. It never recreates measured laboratory data. expected.json is
a frozen numerical oracle and is not regenerated from the current evaluator.
check_assets requires newly generated probe_receipt.json and verifies source
hashes, nineteen numeric values and selected CSV row coverage. Recreating a hash
manifest to hide a failure is not an acceptance step.

The optional NEML source build was previously blocked by missing BLAS_LIBRARIES.
run_neml exit20 means BACKEND_UNAVAILABLE, not pass/skip. If an actual library is
installed, the tensor/scalar numerical comparison can run; numerical pass returns
21/source_correspondence=unverified. An environment variable cannot attest a
pinned build. A separate real build receipt plus loaded extension hash verification
is required by the implementation specification. No Windows NEML installation,
product GUI, native calibration or physical-material adequacy is proven here.

~~~powershell
& $Py "$B\cold_reproduce.py" --workbench-root $WB --destination C:\CAE\B-cold-check
~~~

The destination must be new/empty and outside the source directories. It copies
the bundle/core, denies child reads from both originals with a Python audit hook,
runs references there and verifies that loaded core origins point only to the
copy. NEML exits20/21 are recorded as unmet backend/source gates. A prior version
of this procedure actually passed the six reference/API/integrity commands before
the outage, with NEML20. This reconstructed version is not falsely marked rerun.

No branch, PR, main or product runtime was changed during blob recovery. The final
new blob revision must be read by E/F. The missing original raw artifact bytes and
new execution environment remain explicit limitations.
