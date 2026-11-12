# F independent engineering QA of the reconstructed implementation handoff

Review date: 2026-11-12. Reviewer: F, an independent agent role, not a human certification authority.

The corrected documents are acceptable as a concrete implementation specification within the explicitly selected profiles. No remaining F-owned static blocker was found in the exact document set below. This does not certify a finished product, recovered historical raw results, arbitrary native-model support, an arbitrary material model, or physical material adequacy. Current execution receipts and the final integrated tree remain separate acceptance evidence.

## Exact reviewed material

Repository: pikachu444/caelab-workbench. I read the actual cloud blobs and then the actual recursive Git tree of assembled, unreferenced commit 98be90bfe1d8d02ae099bc0aecf84d1d11110218. The tree binds the following read documents and selected assets except the explicitly noted later B documentation/source update; this is not a claim to have executed or reviewed every baseline repository file. B's later annex f5e122220fb6f6c5706dcb4906832c0d80fded2b was separately read/diffed: only common-core section references and failed-Jacobian/SVD not_computed reporting changed. The executed tree contains its prior annex7c2087cfb92cf3411cb807a94546645982253ecc; no new numerical code is implied by the documentation update.

| Document or asset | Git blob |
|---|---|
| Integrated master | 0a84d2e009dbd891d3115d1fcb036e644628ba9a |
| Common core | b3ec806dd6d5e52c5062c75fc98b1e829b2c08ac |
| A pressure/foam/orthotropy/derived XBP | 1f8fb280e3a0f8413721d9c4eed9f7596a163f4d |
| B finite strain/history/state profiles, latest static revision | f5e122220fb6f6c5706dcb4906832c0d80fded2b |
| C expert implementation | 035b6e431f34e94bac33e29f2f0cc1e796995438 |
| D post implementation | 279e81037d279f0852a40577d32ec4188bfcc599 |
| B typed scalar/constraint contract | 06371aa820c456dec054c1b2d40cb07ca0f25665 |
| B finite-domain equilibrium reference | 7b8b90ae788eb06b388b1b14c4854925dcc2ab0d |
| B synthetic reference generator | 17362315bbdb945aa2d63888088f8a180196930c |
| B expected arithmetic | 922313cc9daba73ace9b1ec40f557d2b5de8d57a |
| B source manifest | 9aa662051395deddf08659513df4e3b91183d3f0 |
| B newly reacquired11-source/8-notice bundle, later addition | b95d68052eb52097fcc0a2f23527edd836c0b108 |
| New F independent B source-integrity check | c569c56ab3cbfcd19e843c90baa63fd86376588c |
| Current F direct remote-CI review receipt | 57b0974cdfe18e9c8d5fc751074ac2fdd9c1470f |
| C real source/license/span bundle | 38c12bf87cc38d4184d4f49213b52654d91d6b3c |
| C corrected development/evaluation questions | 93881aa4a70fc749e9d9c65995398b37a776e902 |
| D reconstructed native checker | 951bc556718bc4388ddcc01a77c998c0363a8b5c |
| D reconstructed checker policy | 2b06f6406a2c5a4d83231db3e2daac1d18e80e1c |
| A reconstructed arithmetic generator | e74ff030057030e1f20edf9bb8890de68abd89ca |
| A reconstructed Ka1 native checker | 81aee286020ac529f4408503caa1ea2cfc281f0c |
| A reconstructed native runner | 37c930034a9f307aa7d98e50c7f464441da555a7 |
| New F source/label semantic review | 16ff7b27ccda91389b81ee34fc3f4aaba1dd024d |

F independently appended recovery journal seq3 to the actual E seq2 blob, producing b50b74535102360d18ad367538f1196f0d868003. It is a new authored entry. Original historical F19 was never posted before the outage and is not restored here. The original full 17-entry journal and the original 316-file package are not reconstructed by this report.

## Corrections caused by current F review

1. A's first recovered XBP equation wrote e=Se+ep. I required e=S*s+ep, with stress s and the engineering-strain/plastic-strain convention retained. The actual replacement was read. This fixes the written equation; it does not turn the explicitly derived GMN2012/tensor-norm variant into original Xia2002.
2. A's energy-ratio denominator was ambiguous because BRIC IE is an energy density, while GLOBAL INTERNAL ENERGY, KINETIC ENERGY and HOURGLASS ENERGY are total energies. The actual text now names those three GLOBAL columns, their Mg*mm^2/s^2=0.001 J unit, excludes BRIC IE as the ratio denominator, and declares the small-denominator floor/not_assessed policy. Ratios are dimensionally meaningful.
3. The recovered core's blanket pass/fail/not_assessed sentence contradicted uncertainty, identifiability and constraint enums. The actual core now enumerates each field, uses the exact registered lateral_min_log_slope_over_mu ID and the empty dimensionless unit string. C initially conflated finite high fit error with INFEASIBLE and then shortened an invalid objective to objective=null. The actual final C text preserves the objective object {eligible:false,fit_loss:null,reason_codes:[CONSTRAINT_RESPONSE_INVALID]}; high error and physical infeasibility are independent.
4. The new evaluation question41 asked for DOLFINx0.11.0 but its gold facet said v0.10.0. Five explicit Radioss/CalculiX queries also lost their solver facet, including CalculiX2.23. I rejected the labels. In actual corrected blob93881aa4, all six facets preserve the question's solver/version and include exact source spans.
5. The first recovered development questions25-36 were one birthday-party question with different case numbers;13-24 were topic-ID templates with mismatched specific obligations. I rejected semantic-diversity claims from literal-string uniqueness. The author supplied12 distinct non-CAE topics and12 explicit engineering missing-source/data questions. I read each changed question/obligation and accepted only this revised fixture scope.

I requested actual author edits and re-read the changed bytes; these findings were not closed from promises. The common EvidenceEnvelope strict union and legacy mapping, parent/child lease rules and full-answer review defects are E-owned integration findings. I read their resulting common/annex changes for F-domain consistency, but E owns the independent closure of those objections.

## Original F01-F11 disposition in the new specification

| ID | Concrete decision retained | What remains outside approval |
|---|---|---|
| F01 DP | GEOS smooth-cone compression axisymmetry only; tensile/apex requests rejected; q, finite state, radial stress, consistency and plastic multiplier checked. The failed arbitrary radial bracket was replaced by the exact mixed-control2x2 consistency update. | No apex/tension/general loading claim. GEOS native execution is a separate backend gate; Windows gets the stated independent analytic peer, not an unverified WSL transport. |
| F02 orthotropy/XBP | Reciprocal plane-stress compliance/SPD, material axes and work-conjugate shear are explicit. GMN2012-derived current tensor plastic norm is named, not accumulated history. n=0.2 normal/shear/rotation references supplement easy elastic/perfect-plastic limits. | Full original Xia2002/27-parameter law and general return-map runtime are not established; reverse plasticity is outside the selected profile. |
| F03 LAW33 | Ka0/Ka1 source branches and CURRENT air-pressure update order are fixed; rate, viscosity, strain/J and energy units are explicit. /DTIX(initial,max) and separate capped baseline/half-step records are preserved. Frozen91-time grid uses actual-time interpolation, no extrapolation, absolute+relative tolerances. | Historical short affine native runs are not long rate/hold/shear campaigns or experimental foam identification. The current remoteCI supplied a new selected Ka1/Euler-checker receipt; it is not the historical DOP853/Ka0 receipt. |
| F04 Ogden | INC2-term and COMP1-term are distinct formulation/model revisions. COMP uses isochoric energy plus K/2*(J-1)^2 and an actual lateral equilibrium solve; J=1 data do not identify K. | INC curves cannot be silently reused as finite-K traction-free predictions; native card support is not claimed. |
| F05 NEML | Exact Chaboche/Voce/RI API and negative internal backstress mapping; fresh candidate/experiment state. Environment variables and package version do not authenticate source correspondence; exit20 unavailable and exit21 numerical-pass/source-unverified remain distinct. | Historical BLAS build failure is not tensor-library success. Actual built extension/source/ABI receipt and Windows installation remain unmet. |
| F06 visco/JC | pyvisco's angular-frequency/concatenated-output API, Prony equilibrium fraction, reduced time and temperature rules are concrete. Physical fraction bounds and separated tau bands replace unresolved simplex choices. JC uses prescribed local state and tension-positive triaxiality. | A synthetic fit is not measured properties; JC initiation is not an FE deletion/regularization implementation. Single rate/temperature/triaxiality data cannot identify every coefficient. |
| F07 native/post | Actual converter source checkout is separated from source-file last-change and release identity. Field/history times, layer/family/ID/basis, BRIC energy density and impulse-secant provenance are explicit. | Release CI did not pin Tools; byte-identical historical output is not a compiler/build certificate. Derived force remains unverified for physical reaction fitting. |
| F08 expert | Current actual8-source/16-span material and corrected36dev/60eval labels have a new F semantic review. Same frozen physical context and exact result identity are retained through tools/review. | Small paired-intent annotation is not measured general retrieval or live-provider engineering quality. E's wire/lifecycle review remains independently owned. |
| F09 optimizer | Every scalar declaration carries schema_version1. Reserved host fit_loss plus registered finite scalars feed existing optimizer constraints. Mandatory profile constraints cannot be dropped. Finite physical instability keeps finite loss/INFEASIBLE; missing/nonfinite required scalars invalidate the objective. | This is a common-service implementation contract. Historical existing-optimizer probes are not a new material-service execution. |
| F10 COMP roots | Bounded domain[1e-4,1e4],129log nodes, signed monomial bounds for R and R',64epsilon guard,20subdivision levels, Brent tolerances, root merge, residual test and exactly-one-root/refusal rules are fixed. | Finite-domain numerical screening is not a formal real-arithmetic global uniqueness proof. Tangent/ambiguous/unresolved roots are not silently selected. |
| F11 layer completeness | Exact required array set, TIME/tensor shapes, ID uniqueness, family/part and node geometry checks replace iteration over merely present arrays. New checker explicitly rejects deletion, renaming, ID-only swaps, shape and time defects; consistent permutation is permitted. | Homogeneous native membrane fields do not prove physical through-thickness placement. The distinct synthetic middle5/upper10 test verifies selection logic only. |

These closures concern the corrected design decisions. None deletes an unmet backend or physical acceptance gate.

## Source-law and numerical review

A's GEOS source is d81a4438f107446f17e33ab70c70358935e473ab, with actual DruckerPrager.hpp blob02e2a44fcf7ad2c53541533dcc126d9f53961cb6 and TriaxialDriver.cpp d266ae01a44380501e6fedcb5dda2339d2f1d8fd. The code has a20-iteration local loop without a final exhaustion throw and no general apex return. The selected restricted branch and independent residual checks therefore matter. Compression-positive reporting must remain a conversion from tensile-positive mean stress, not a sign change inside the source law.

LAW33 source cb760c206e346eff12161a2268b134ef9d48a4ef/sigeps33.F blob62b137d2f62a5b101aced76bdf9d69c41cc33092 uses current air pressure in the source update ordering. Ka1 viscosity has stress*time units and tensor/engineering shear factors differ. The selected independent ODE and exact kinematics are appropriate narrow numerical references. Total-energy diagnostics and BRIC per-reference-volume energy cannot be interchanged.

B's Ogden2mu/alpha^2 convention is tied to adtzlr/hyperelastic58b036acc389585110f5d74e0457c36fae0cc4f2, not a name-only equivalence. COMP stress is (beta_i-mean(beta))/J+K*(J-1); nominal stress is J*sigma*F^-T. I checked the reconstructed uniaxial/biaxial/planar monomial root expressions against those formulas. The new code is concrete and was statically read, not re-executed here.

The five B scalar declarations and every constraint reference were checked against the actual JSON. Bulk ratio bounds10..1000, normalized acoustic minimum>=1e-6, derivative discrepancy<=1e-3 and normalized minimum lateral log slope>=1e-6 use their stated scales and zero tolerance. These finite physical constraints are separate from numerical inability to compute an equilibrium or required scalar.

pyvisco is pinned to b61b5a0c6a2939927c87189972ea5619503bd813. Its actual source spans specify time/tau in seconds, omega in rad/s, storage then loss concatenation and base10 WLF shift. NEML65ab2a648a975e2e9de8e1371be270567f9683ac uses dev(sigma)+X with negative internal evolution; a direct sigma-X external-state substitution would be wrong. B's JC source d791761d9636abaa27e41ebbe271fa9df519287e is intentionally not equated with D's separate cb760c release binary.

Identifiability is part of acceptance, not a low-loss badge: K atJ=1, WLF constants atTref only, full JC flow at one rate/temperature, and full failure coefficients at one triaxiality/rate/temperature are rank-deficient counterexamples. State initialization, held-out tests, fixed residual scaling, full required coverage, repeated-strain branch/time order, sensitivity step checks and not_computed/not_identifiable reporting are retained.

## Current C review actually performed

I read all16 selected source spans and all60 evaluation questions/obligations, then all changed development questions. Current functions-JS checks independently confirmed exact Unicode-codepoint extraction, line endpoints and parent-hash metadata agreement for16/16spans. After correction,96/96question solver/version spans matched their actual text; the96 full question strings are distinct. Development counts are12supported/12insufficient/12non-CAE; evaluation counts24/18/18. The24 covered evaluation items are12paired intents.

The source semantics cover Poisson weak-form signs; six rigid modes; Lame conversion; deviatoric von Mises; F/C/J and logarithmic neo-Hookean energy; Cauchy versus energy-gradient stress; GEOS old cohesion/angle conversion; Prony equilibrium/frequency; WLF log shift; and NEML block state/sign. Missing solver defaults, contact law, original Xia, user results and experimental identification are not fabricated. The27Korean-English terms and6language cases are acceptable only as reviewed retrieval projection retaining original text, negation, conditions, numbers and solver/edition tokens. Unverified language coverage and absent source coverage remain different outcomes.

E separately reported current full SHA256/UTF8/source/license/span integrity checks. I do not relabel those E executions as F executions. The new F receipt binds exact blobs, rather than rewriting source or label bytes to pretend the old original review still applies.

## Historical independent observations, not fresh cloud executions

Before the environment failure, F independently ran or cross-checked the following. Their numerical values are historical observations. This report does not recreate their original logs/CSV/receipts.

- Mixed-control DP versus independent piecewise algebra: stress difference below1e-6Pa and strain below1e-13. Foam current-air counterexample, Ka1 exact scalar ODE and n0.2 XBP normal/shear/rotation checks passed their stated narrow references.
- COMP energy-gradient finite differences at stretches(.8,1.1,1.2): maximum error4.20e-10MPa. Independent visco convolution quadrature versus recurrence:3.47e-18.
- Actual existing Workbench optimizer with actual pyvisco:146observations, converged synthetic loss1.43218e-13; fixed-WLF held-out temperature loss3.29118e-14. It was neither an implemented material service nor a measured-material validation.
- Actual short Linux LAW33 output versus F's independent DOP853/projected1D calculation: maximum stress error Ka0 about2.70928e-7MPa, Ka1 about3.37898e-6MPa. Nodal displacement, J/rho/volume, other stress components and added-mass/hourglass checks were also assessed.
- Actual /DTIX2e-6 and1e-6 capped Ka1 pair, independently verified effective caps and actual times, fixed91-point0..0.045s grid: maximum refinement difference7.56674e-6MPa; absolute+relative oracle/refinement policies passed. This did not replace the original uncapped run.
- Actual affine TH106columns and source times, independent Hooke transfer, and F-owned corrected D checker passed. The old missing-layer bug was reproduced on actual raw data; corrected7negative cases were rejected and a consistent point permutation was accepted.
- Historical COMP243-config/reversed-order/four-negative equilibrium probe, existing ConstraintSpec3cases, existing MDO criteria4cases and policy11cases were independently run. Finite high error/finite instability retained objective; missing/NaN required scalar did not.

The original complete raw package is unavailable. Some input and summary files were separately recovered or reacquired; their existence does not replace all lost native output. The reconstructed A checker now uses a new fixed-step explicit-Euler oracle at1.25e-7 and only the capped Ka1 pair. It must not inherit the historical F DOP853 receipt or imply a new Ka0 run. The reconstructed D checker adds an eighth explicit tensor-shape rejection; historical seven-case results do not certify that new code.

## Current remote execution independently inspected by F

After the static report was drafted, I directly fetched GitHub job130925841559, run43140439077 logs, job/step metadata and artifact metadata. All selected steps concluded success. The raw decoded log has86634characters; stripping only ANSI CSI sequences yields exactly the86480characters of root's saved clean log blob39c9d20285cba77b07eee5ec4f9389e99fc31577. My new inspection receipt is57b0974cdfe18e9c8d5fc751074ac2fdd9c1470f.

The PR head was98be90bfe1d8d02ae099bc0aecf84d1d11110218. The actual checkout was merge commitf975d44142408c491a4fad083f24e7945cb0a086, as printed by checkout/git log. These are different identities and neither is the later final documentation commit.

Observed new execution scope:
- A reconstructed arithmetic/deck recipes passed. The explicitly selected official Linux executable hashes were checked, then Ka1 capped2e-6/1e-6 native runs and the newly reconstructed fixed-step Euler checker passed.
- D reconstructed native-deck generation passed. This workflow did not run D's solver/converter/native-layer checker.
- B independent arithmetic/reference checks,243finite-domain COMP configurations plus reverse-order/four-negative cases, actual pyvisco2.1.3 source-hash/API checks,19expected numeric checks and17static-asset checks passed.
- Existing Workbench optimizer plus actual pyvisco converged with146observations, loss1.4321823225436936e-13 and untouched holdout loss3.291180473817548e-14. WLF coefficients were fixed. Existing constraint API three-boundary-case results retained finite0.02loss for high-error stable and finite-instability cases and invalidated a required numerical scalar failure.
- Relocated cold-copy references/API/optimizer/constraint checks passed with explicit core source/hash/origin checks and denial of original bundle/core reads. NEML returned20, unavailable; this unmet gate was explicitly preserved rather than counted as tensor-backend success.

Artifact13909436920 is2029916bytes, digestsha256:916e3aedf36533d23dec9d971b699e18ca9026f5cce42150c758b49f21fa18ec. Direct GitHub metadata says it expires2027-02-10T02:18:08Z. Upload logs include the new native outputs and receipts. F inspected the actual log and artifact metadata, not every archived binary/CSV; an independent raw-array revalidation is not claimed.

This is fresh selected reference evidence. It does not rerun historical Ka0/DOP853 or D native layers, recover original outputs, validate general GEOS/XBP/NEML, or implement the material service/Windows/provider/RP product flows. Newly reacquired B source bundle11files/8notices was separately fetched. F independently implemented SHA256 over UTF8 codepoints, checked known empty/abc vectors, then recomputed all11source and8notice digests in current functions-JS execution. All matched the actual bundled fields; all11source hashes also matched the preexisting fixed source manifest, and all11declared byte lengths matched. Receiptc569c56ab3cbfcd19e843c90baa63fd86376588c records this new integrity check. This later handoff addition is not silently included in the earlier CI checkout, and matching source bytes do not authenticate compiled NEML or solver binaries.

## Completion boundary

The specification preserves the original broad material/expert/post goal. DP/Xia remain examples, and J2 or one easy model cannot stand in for the whole requirement. Concrete adapter decisions now connect parameters, units/state, exact source/API/card scope, experiment protocol, observables, residual/constraints, identifiability, persistence and acceptance. Registering a new law is still not support for its calculation.

A reviewable documentation/reference branch can carry this static approval when its paths bind these exact blobs and the new authored review/journal. The new remoteCI log and exact scope below support selected reconstructed reference executions. Python/native execution was not launched in F's unavailable primary runtime; F independently inspected the actual remote job/log/metadata and did not independently reparse all new native raw files. Later integrated documentation/proof/source additions must preserve exact executed code identity or obtain a new applicable execution receipt.

Product CalibrationStudyService/GUI, actual provider loop and engineering answers, opened-Prep Apply/Undo, exact optional RP fork, Windows process/render/path behavior, actual NEML tensor backend and independent real-material experiments remain explicit implementation/acceptance gates. E-owned final wire/lifecycle findings and the root's final integrated-commit verification must be closed on actual bytes independently of this F report.
