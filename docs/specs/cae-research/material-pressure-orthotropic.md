# A. Pressure dependent, crushable foam and directional paper implementation specification

RECOVERY STATUS: this is a NEW RECONSTRUCTED specification after the execution environment became unavailable. It does not claim byte identity with original final part SHA256 5d913c95bc5a0f8bfd9deeb6faf5dda690b8cd246e1d456f478ea4922b97f5ff. It preserves the selected laws, equations, code identities, public interfaces, numerical recipes, actual reference results and explicit limits from the authored R3. Original raw native TH/CSV/log bytes have not been reconstructed by guessing. Review this exact recovered revision before approving the handoff.

This is a planned product capability specification with some real Linux reference executions. New material services, general XBP update, GEOS execution and Windows product acceptance are not implemented or completed by this document.

## 1. Selected profiles and verified source identities

| Profile ID, revision 1 | Computation | Exact supported domain |
|---|---|---|
| geos_dp_axisym_linear_v1 | Existing GEOS DruckerPrager and TriaxialDriver | Linux-host serial; small-strain compressive axisymmetric smooth-cone branch; constant friction/dilation and linear cohesion hardening |
| dp_axisym_closed_v1 | Exact analytic peer of the same selected branch | Windows-capable material-point computation; diagonal strain control and exact mixed triaxial control; no full FE solver |
| oc_foam33_ka0_v1 | Existing OpenCourant LAW33, Ka=0 | Compressible principal-stress crush limit with elastic unloading; rate-independent skeleton |
| oc_foam33_ka1_v1 | Existing OpenCourant LAW33, Ka=1 | Distinct source-defined rate-modulus/viscoelastic skeletal ODE plus principal clipping |
| ortho_plane_stress_elastic_v1 | Exact orthotropic compliance | MD/CD plane stress only |
| gmn12_xbp_power_plane_stress_v1 | New material-point constitutive update | Explicit GMN2012-derived reduced-XBP power-hardening/tensor-norm operational variant; monotonic plane-stress loading and elastic unloading |

A-GEOS: GEOS-DEV/GEOS revision d81a4438f107446f17e33ab70c70358935e473ab, LGPL-2.1-only. Source paths relative to repository:

- src/coreComponents/constitutive/solid/DruckerPrager.hpp, git blob 02e2a44fcf7ad2c53541533dcc126d9f53961cb6.
- src/coreComponents/constitutive/solid/DruckerPrager.cpp, blob a64ae00030cbd75a322b30ae66591d1c0521c9a9.
- src/coreComponents/constitutiveDrivers/solid/TriaxialDriver.cpp, blob d266ae01a44380501e6fedcb5dda2339d2f1d8fd.
- src/coreComponents/constitutive/docs/solid/DruckerPrager.rst, blob dcb4ccafe49e5165912f4558288e81ff31d55754.
- inputFiles/constitutiveDriver/triaxialDriver_DruckerPrager.xml, blob dc244cae36460669bf436982ed00083b637f1e76.
- inputFiles/constitutiveDriver/triaxialDriver_base.xml, blob f93a592f81d6cf5256b5aa6e96edaf1326efcb5c.
- LICENSE, blob 8c177f8bedc4a734d40312bb165035be1b22fad4.

A-OC: OpenCourant/OpenCourant revision cb760c206e346eff12161a2268b134ef9d48a4ef. Source headers are AGPL-3.0-or-later. No source is linked into the product merely by registering the external executable.

- engine/source/materials/mat/mat033/sigeps33.F, blob 62b137d2f62a5b101aced76bdf9d69c41cc33092.
- starter/source/materials/mat/mat033/hm_read_mat33.F, blob 84132a6b22bbd439ff0aec6e5bf427a22ea1f560.
- engine/source/input/freform.F:576-595, blob 79abd582b3bb4ad0ef363a2df68d129f0c4318ab: DTIX parsing and defaults.
- Radioss2022 format CFG matl33_foam_plas.cfg, blob 154a244ca95482ee14f3a30a856055207f350c52, was inspected as format documentation only. Its independent restrictions do not license product copying.
- LAW102 engine blob 85fc54763fb355614073ee9e45a6478dbe46ee66 and Starter blob d6f8541268c950a9c88d6b6bd851c7220547f66a were inspected. This law clips deviatoric stress at a pressure-dependent bound and is not substituted for the selected dilatant/hardening GEOS law.

A-XBP02: Xia Q.S., Boyce M.C., Parks D.M. (2002), International Journal of Solids and Structures 39,4053-4071, DOI 10.1016/S0020-7683(02)00238-X. Bibliographic identity verified; original full text and an openly licensed reusable original UMAT were NOT acquired. Full original 27-parameter/tanh XBP is not implemented or claimed audited.

A-GMN12: Garbowski, Maier, Novati (2012), Structural and Multidisciplinary Optimization 46,111-128, DOI 10.1007/s00158-011-0747-3, open article https://link.springer.com/article/10.1007/s00158-011-0747-3 . Sections 3.1-3.3 and equations 1-9 were read. It describes a reduced power-hardening XBP and an Abaqus6.9 subroutine, but supplies no openly licensed UMAT. Our implementation is independent and explicitly fixes a tensor plastic-strain norm convention. The article's pseudo-experiments are not independent real-material validation. Hill LAW32/43 or LAW19/58 fabric are not Xia.

OpenSees DP was investigated but its COPYRIGHT restricts use/distribution; it is not the default redistributable backend. GEOS/OpenCourant external distribution must preserve their respective notices and TPL conditions. Product installer need not bundle either program.

## 2. Shared interfaces, ownership and assessment

Use the integration's single ArtifactRefV1, ResultRefV1, MaterialStudyV1, ModelDescriptorV1, ExperimentSpec, ComparisonSpec, EvaluationPlan, PredictionBundle, CalibrationEvaluationArtifact and AssessmentResultV1. Do not create a universal FE schema, alternative result DTO, database, solver queue or optimizer.

The proposed MaterialAdapter protocol is capabilities(model,environment), validate(model,parameters,experiments), plan(request), collect(plan,artifacts), export(request). Proposed A modules are materials/adapters/geos_dp.py, dp_axisym_closed.py, opencourant_foam33.py, orthotropic_plane.py, gmn12_xbp.py. Deterministic history extraction is registered in materials/native_history.py. Profile equations/constraints stay in adapters; service/GUI have no model-name switch.

Material CSV, driver outputs, parameterizations and reports use consumer owner material-study:<study_id>. Real native run outputs retain run ownership; GEOS driver text is a material experiment artifact, not a fake FE field ResultRef. Required TH observables use D's single registered history extractor and DatasetManifest dataset_kind=history/series contract; RP field display does not establish calibration evidence.

External execution belongs to existing JobManager/RunStore. Fresh initial state per candidate x experiment; all ordered load/unload segments of one experiment preserve state. Unsupported cross-experiment restart is rejected, never inferred. Time remains strictly increasing through strain reversals. No strain sorting or invented t=0 row. Store raw immutable CSV bytes and conversion recipe.

Each CSV needs time_s, all controls and observations with explicitly declared units, measure, sign, material/global frame, gauge/selection, preload, specimen thickness/cross-section, test rate, temperature/RH/moisture basis and replicate group. Missing metadata is EXPERIMENT_METADATA_MISSING, wrong/unknown measure MEASURE_MISMATCH. No guessed units. Mechanical units use the shared unit layer; any temperature conversion uses central materials/units.py, not profile-specific offsets.

C7 revision canon: expected_revision uses mutable document_revision. MaterialEvaluationRequest.study_revision, cache keys and OptimizationProblem.revision use immutable content_revision. Progress/index/selection persistence does not change frozen campaign content. Adapter output remains tied to the frozen content revision.

C1 assessment canon: only train comparisons with required_for_fit=true enter the fixed objective; validation/holdout required_for_validation=true define their split acceptance. Diagnostic comparisons have both flags false and cannot change the denominator when they fail. Use frozen observed-grid interpolation and normalized trapezoid weights, fixed positive scales and separately frozen channel/test weights. No extrapolation or candidate-dependent sample crop. Holdout never trains or sets scales. Objective and assessment are serialized only in their canonical fields.

Valid high loss remains finite and eligible. Required train execution/integrity/measure/coverage failure produces objective.eligible=false, fit_loss=null and EvaluationObservation(valid=false,reason,responses={}); no fake physical penalty. Valid holdout failure does not erase train loss. Missing physical acceptance limits means not_assessed. Optimizer convergence does not certify identifiability or adequacy.

C6 capability canon: every ScalarResponseSpecV1 has schema_version=1; register the host-owned reserved fit_loss response with unit="" (dimensionless) and source_kind=residual. The adapter does not put fit_loss into PredictionBundle.optimizer_scalars; the host computes it from residual artifacts. Use optimizer_constraints=() for these A profiles' already encoded supported domains; differentiable=true only for elastic orthotropy, false for DP/foam/XBP. Arrays remain artifacts. Do not create parallel scalar constraint fields.

Optimizer: orthotropic elastic SLSQP seed17,max_evaluations400,max_iterations120,ftol1e-10,finite_difference_step1e-5 in scaled free coordinates. Nonlinear DP/Ka0/Ka1/XBP use existing DE seed17,max_evaluations1000,max_iterations100,population_size20,max_failures10,wall_seconds3600,ftol1e-8,best1bin,mutation(.5,1),recombination.7,polish=false,no surrogate. Existing DE's temporary infinity ranks invalid search proposals only; never serialize it as measured loss. SLSQP stops on first invalid point. Failure termination retains valid archive.

Identifiability: whitened residual Jacobian on required training channels, scaled finite differences, SVD rank threshold s_i/s_max>=1e-6 and full-rank condition<=1e6. Repeat derivative steps .5x/2x; unstable rank yields identifiability_status=not_computed with NUMERICAL_SENSITIVITY_UNSTABLE, mapped to adequacy not_assessed if identifiability is a required policy report. Correlation>.995 and bound contact warn. These are experiment-local diagnostics, not certified uncertainty.

Export writes new bytes plus manifest, not open Prep mutation. For DP analytic and derived XBP a parameter/equation report is not a native solver card. A profiles remain unsupported for Prep Apply until an actual provider card/curve/orientation single-transaction codec passes its own tests.

## 3. Host support and GEOS cancellation decision

Windows DP material research uses dp_axisym_closed_v1 without GEOS/HPC/Linux. Selected native GEOS is Linux-host x86_64 serial ONLY. Windows-native and WSL GEOS invocation are unsupported: host can_run=false, CapabilityReport.supported=false, reason_codes=(UNSUPPORTED_HOST_TRANSPORT,), zero dispatch. No automatic WSL candidate availability.

Linux configuration supplies a real local GEOS executable identity. Exact argv [absolute_geos,"-i","case.xml"], cwd=fresh run directory, OMP_NUM_THREADS=1, closure snapshot/hash through existing native job ownership. Existing Linux jobs.py _native_exited retains leader PID with waitid/WNOWAIT; _finish_owned controls only its recorded live process group. Do not adopt restored PIDs or auto-run on reopen.

E-05 established that closing the Windows Job Object for wsl.exe cannot attest Linux descendant cleanup. R2's direct-WSL proposal was withdrawn. A considered supervisor protocol was not implemented; root chose Linux-only native support rather than making a new remote transport a prerequisite. No normative WSL argv/supervisor contract remains. A future WSL backend needs a separately implemented/reviewed transport before registration; this is a resolved current support decision.

Required future native Linux tests: executable/source identity, missing installation, fresh state/order inversion, paths with spaces/Korean text, snapshot/output mutation, compute/collect cancel and group cleanup, reopen without launch. Actual GEOS execution remains unperformed. Windows negative-capability tests must dispatch no GEOS job while an analytic DP study remains executable.

## 4. Drucker-Prager exact formulation and computation

Tensile-positive stress/strain. p=tr(sigma)/3 is negative in compression, display pressure P=-p. s=sigma-pI, q=sqrt(3/2*s:s)>=0. SI Pa,s,kg/m3. MC compression-corner conversion:
a0=6*c*cos(phi)/(3-sin(phi));
F=6*sin(phi)/(3-sin(phi));
D=6*sin(psi)/(3-sin(psi)).
Yield f=q+F*p-a, potential g=q+D*p, dep=dlambda*dg/dsigma, da=h*dlambda.

State is committed stress6 and cohesion a; reconstruct plastic increment diagnostics from full strain/stress history relative to initial stress. K,G,c,density>0,h>=0,0<=psi<=phi<60deg,a>=0,dlambda>=0. Constant friction/dilation, no kinematic hardening. No tension cutoff, apex return, cap compaction, damage, fracture-energy or softening parameter exists in this profile. Effective/total stress basis must match experiment; do not silently subtract pore pressure.

Diagonal strain-control exact analytic peer:
p_t=p_n+K*tr(de);
s_t=s_n+2G*dev(de);
q_t=sqrt(3/2*s_t:s_t);
f_t=q_t+F*p_t-a_n.
Elastic if f_t<=max(1e-8Pa,1e-12*stress_scale).
Otherwise dlambda=f_t/(3G+K*F*D+h);
p=p_t-K*D*dlambda;
q=q_t-3G*dlambda;
a=a_n+h*dlambda;
s=(q/q_t)*s_t.
Require positive denominator,q_t>1e-12*stress_scale,q>=1e-10*stress_scale,p<0. Apex/tensile crossing returns UNSUPPORTED_STRESS_PATH; never clamp negative q into an invented cap. Pure hydrostatic compression is elastic and bypasses q division.

Mixed-control compression branch has an exact 2x2 solve, not a fixed invalid radial bracket. Define dr=target_radial-old_radial, A=F/3-1,B=1+2F/3,C=D/3-1,T=E*de_ax+2*nu*dr. Trial dsa=T and ftrial=A*(old_axial+T)+B*target_radial-aold. If elastic dlambda=0. Otherwise dlambda=ftrial/(h+E*A*C),dsa=T-E*C*dlambda. Then de_rad=(-nu*dsa+(1-nu)*dr)/E+dlambda*(.5+D/3), sa_new=sa_old+dsa,sr_new=target,a_new=aold+h*dlambda. Validate final p<0,q=sr-sa>=0 and multiplier>=0. Numerical bracket trial states must not be confused with actual requested path support. The earlier radial [-.1,.1] bracket failed even a first elastic compression increment because its high endpoint made p tensile; this counterexample was reproduced and the bracket removed.

Native XML: Events/SoloEvent target /Tasks/driver; Constitutive/DruckerPrager name dp with defaultDensity,defaultBulkModulus,defaultShearModulus,defaultCohesion,defaultFrictionAngle,defaultDilationAngle,defaultHardeningRate. Tasks/TriaxialDriver name driver,material dp,mode mixedControl,axialControl axial,radialControl radial,initialStress,steps100,precision16,output dp_native.txt. Functions/TableFunction time coordinates/values. No FE mesh conversion.

Fixture K1e9,G6e8,c1e6,h5e7Pa,phi30deg,psi10deg,rho2700,initialStress=-2e6Pa; axial strain0->-.01 over1s, radial stress constant-2e6. GEOS output strain is zero relative to this prestress; do not add preload elastic strain implicitly. Parse exact nine header columns: time,strain,axial; strain,radial_1; strain,radial_2; stress,axial; stress,radial_1; stress,radial_2; newton_iter; residual_norm (the actual file is whitespace with header meanings). Require steps+1 complete finite rows, increasing actual time, both radial channels. eps_v=ea+er1+er2; p=mean normal stress; q=abs(sa-sr) only after radial equality. Triaxial q is deviatoric/shear evidence, not a pure simple-shear capability. Controlled gamma12 requests return UNSUPPORTED_OBSERVABLE with no job. StrainControl equal strains gives hydrostatic test; radial zero gives oedometer.

GEOS source hazard: local update loops20 times without a final convergence throw, has no robust apex branch and divides newQ/trialQ. Exit0 is insufficient. Every native row must pass actual coverage, finite values, radial stress residual, driver residual<=1e-8, q>=0, reconstructed plastic multiplier/cohesion/yield check |f|<=1e-7Pa+1e-6*stress_scale. Driver early-failure trailing rows do not establish coverage. Cohesion reconstructed from strain/stress increments is labeled an operator diagnostic, not a reported internal field.

Training: drained triaxial P2/P5MPa paths0->-.01->-.008->-.012, axial stress and both transverse gauges/volume/q/P; separate hydrostatic compression equal strains0->-.003 for K and low-strain deviatoric elastic data for G. Holdout independent specimen P3.5MPa and2x slower rate. Only one confinement confounds c/phi; fix one or report not identifiable. Missing transverse strain fixes psi rather than claiming identified dilation. Demonstration bounds K[.5,2]GPa,G[.3,1.2]GPa,c[.5,2]MPa,phi[15,45]deg,h[20,100]MPa; free dilation_fraction[0,1] links psi=fraction*phi. Staged elastic/plastic identification followed by joint fit only when data rank allows it.

Independent constant-confinement fixture: E1.5e9Pa,nu.25,F1.2,D.3686338897262739,a0=2078460.9690826528Pa. Yield q7464101.615137753Pa at ea=-.0049760677434251685; plastic slope89348488.7169089Pa. Final sa=-9912982.369678844Pa,er=.004674005556472009,eps_v=-.0006519888870559817. Exact peer tolerance1e-7Pa+1e-10|expected|,strain1e-11; native10Pa+1e-5|expected|,strain1e-7,all rows. Independent piecewise versus corrected2x2 calculation historically passed stress<1e-6Pa/strain<1e-13. Test zero dilation,phi=psi=0 J2 limit,h0,extra confinement,hydrostatic compression,elastic unload E/nu,state isolation,missing row,wrong headers/units,cancel and unsupported apex/tension/simple-shear. GEOS native acceptance is unexecuted.

## 5. LAW33 compressible foam and native binding

These are principal-stress crush laws, not an elliptical Deshpande-Fleck model. Current gamma=rho0/rho-1+gamma0=J-1+gamma0; compression gamma<0. p_air=max(0,-P0*gamma/(1+gamma-Phi)). Require denominator>=.05,J>Phi+.05; no extrapolation into lockup.

Actual source ordering is important: compute CURRENT-density p_air first; add this CURRENT p_air to previous total normal stress to form skeletal trial, apply update/clipping, then subtract that SAME CURRENT p_air. There is no stored previous-air addition. A nonzeroP0 single-update oracle distinguishes the two orderings; the corrected law is retained even if another split seems physically intuitive.

Y(gamma)=Fscale*curve(gamma), or curve_id0 gives abs(A+B*(1+C*gamma)). Starter's curve2/rate multiplier is hidden/disabled and is not a supported exposed rate curve.

Ka0: skeletal normal trial +=E*de_ii; shear trial +=E*dgamma_ij/2, zero-Poisson skeleton G=E/2. Diagonalize current skeletal stress, clip each principal to [-Y,+Y], rotate back, subtract p_air*I. Committed stress preserves irreversible unloading/reloading. Table abscissa is total volume gamma, not plastic axial strain. Ka0/Ka1 read a tensile-cutoff field but their selected source branches do not apply it. No independent tension cutoff is advertised; Ka2 would be a new verified profile.

Ka1: Eeff=max(E,C1*max(abs(normal_rates),abs(engineering_shear_rates))+C2).
Normal ODE:
dot(sig_s)=Eeff*dot(eps)-(Eeff+Et)/eta_n*sig_s+Eeff*Et/eta_n*eps.
Shear with tensor eps12=gamma12/2:
dot(tau)=Eeff*dot(eps12)-(Eeff+Et)/eta_s*tau+2*Eeff*Et/eta_s*eps12.
Then principal clipping and p_air subtraction. Source uses explicit Euler each actual step. State includes skeletal stress and total strain. E/C2/Et have stress units,C1 and eta_n/s have stress*time. No invented Maxwell spectrum. Rate/hold/dissipation validity is tested for declared paths, not assumed globally.

Independently authored material card layout:
- /MAT/LAW33/1 then title.
- rho20wide; reference density omitted equals rho0.
- E columns1-20,Ka21-30,curve_id31-40,Fscale41-60.
- P0/Phi/gamma0 each20wide.
- A/B/C/sigma_tcut each20wide.
- Ka1 only C1/C2/Et/eta_n/eta_s each20wide.
- Curve mode uses fixed FUNCT101 with gamma/stress pairs20wide; increasing gamma knots once on import, test path order separate; every candidate's gamma range covered, no extrapolation.

Fixture mm-Mg-s,rho1e-10Mg/mm3=100kg/m3,E10MPa,P0=0,Phi.1,gamma00,A.1MPa,B=C0,Fscale1,tcut100MPa ignored. Ka1 C1.1MPa*s,C2=10MPa,Et2MPa,eta_n=eta_s=.5MPa*s. Validity rho/E>0,0<Phi<1,Et>=0,eta>0,C1/C2>=0. Curve mode fixes unused A/B/C. Reject abs-law parameter ranges whose internal value crosses0. Density/Phi/P0 require independent evidence; do not infer mass density from stress alone.

Native cube1mm,nodes1..8 standard ordering (0,0,0),(1,0,0),(1,1,0),(0,1,0),(0,0,1),(1,0,1),(1,1,1),(0,1,1),brick1,part1,material1. Property Isolid1,Ismstr4,Iale0 with explicit zero artificial viscosity/hourglass in the affine reference; effective values confirmed by actual Starter probes. No contact/gravity/damping/mass scaling. All y/z fixed; left x BCS0; right nodes2,3,6,7 prescribed through IMPVEL1 function1,directionX,skew/sensor0,group2,Icoor0,scales1/1,start0/stop.05. Zero IMPVEL amplitude defaults1, so stationary planes use BCS rather than amplitude0.

Frozen velocity knots (s,mm/s): (0,0),(.001,-1),(.019,-1),(.020,0),(.021,1),(.029,1),(.030,0),(.031,-1),(.039,-1),(.040,0),(.05,0). Ramps and integrated displacement are part of the recipe. Read actual nodal deformation/volume; do not pretend this is an exact exponential stretch. Hydrostatic variant controls each nonzero-coordinate plane with the same path and fixes zero-coordinate planes. Other element formulations are new profile revisions.

Actual output card contract coordinated with D:
TH/BRIC/1 titleBRIC,10wide tokens SX SY SZ SXY SYZ SXZ DENS VOL IE BULK; element row10wide ID1+10blanks+label foam.
TH/NODE/2 titleNODE,10wide tokens DX DY DZ VX VY VZ; each node row10wideID+10wideframe0+label n<ID>.
Engine TFILE/4 interval.0005 and TH/TITLE. Converter may reorder stress/IE/DENS/BULK/VOL; match raw/normalized headers plus IDs/titles and source TITLES, never requested ordinal. Example normalized key BRIC 1 foam SX. TIME is actual source time. Product uses D's registered three-argument short-basename invocation [absolute_exe,"inT01","history"], output history.csv; observed historical upstream one-argument probes remain historical, not an alternate product dispatcher.

Tools actual source-build identity is 1b6bb9200ee7dca899e61c18bd982c3fb1c72fd1; individual last-change revision is distinct. Official release built-in binary identity is a separate SHA and is not inferred from unpinned release CI Tools branch. C converters have their own permission notices, not solver/RP AGPL.

Fit channels are actual Cauchy stress6 at one global solid IP,rho/VOL,node IDs/U/V,time. J=rho0/rho requires mass conservation/no scaling and agrees with VOL/V0 and nodal det(F) within1e-5. thsol code corrects Lagrangian VOL using rho0/rho despite an older misleading comment; use code and actual data. No automatic IP averaging. Force is only an explicitly defined boundary resultant or separately validated operator; not stress summation. Required unsupported quantity refuses calibration before dispatch, missing required output after run is COVERAGE_INCOMPLETE. RP display cannot supply numerical truth. Leapfrog VX is half-step located and is not compared to current-TIME command velocity without its actual offset.

Ka0 training: hydrostatic lambda1->.98->.99->.98; uniaxial strain lambda_x1->exp(-.02)->exp(-.01)->exp(-.02),transverse1. Required stress/volume. P0=0 predicts zero transverse skeleton stress for uniaxial strain. Holdout independent constrained compression J.975 and unload. Incompatible recoverable/rate/hysteretic evidence fails model adequacy, not optimizer execution.
Ka1 training: true rates.01,.1,1/s; log compression-.02,hold2s,unload same rate, both hydrostatic/uniaxial. Pure shear gamma.02+2s hold required if eta_s free; otherwise fixed/unidentified. Holdout rate.05/s and independent unload. C1/eta/Et require transient+hold to distinguish. Demo E[5,20],Yscale[.05,.2],Et[.5,5]MPa,C1[.01,1],eta_n/s[.1,2]MPa*s;rho/Phi/P0 fixed. Fixed gamma knots may use positive monotone strength increments Y_j=Y0+sum(exp(z_j)) ordered by compression; optimize named scalars, not arbitrary table edits.

Ka0 independent limit: exact total-log-strain increments0->-.02->-.01->-.02,E10MPa,Y.1,P00 produce stress-.1,0,-.1MPa at path endpoints,J=exp(eps). Distinguish this increment oracle from finite piecewise-affine native control. Nonzero-air source-order unit oracle: P0.1MPa,Phi.1,Jold.9,Jnew.91,old total=-Y-pair_old,unload increment.002; current-pressure addition differs from incorrect previous-pressure split. It is a kernel unit check, not a consistent full FE increment.

Separate Ka1 integrating-factor oracle: rate-.02/s for1s,hold2s,unload+.02/s for1s,E0/C2=10,C1.1,Et2,eta.5. Cap inactive. Exact solution of sigma'+a*sigma=E*r+b*(eps0+r*t),a=(E+Et)/eta,b=E*Et/eta:
sigma(t)=sigma0*exp(-a*t)+(E*r+b*eps0)/a*(1-exp(-a*t))+b*r*(t/a-(1-exp(-a*t))/a^2).
Compare source Euler at1e-5s with rel1e-3+abs1e-6MPa and separately test active clipping.

Numerical acceptance: prescribed U tolerance1e-7mm+1e-4|expected|,Ka0 and Ka1 each sample stress absdiff<=1e-5MPa+.005|oracle|,J relative1e-5. No mass added/deleted element/negative volume/nonfinite energy. Energy diagnostics use the GLOBAL CSV columns exactly named INTERNAL ENERGY, KINETIC ENERGY and HOURGLASS ENERGY, all total energies in the same declared unit (Mg*mm^2/s^2 = 1e-3 J for this fixture). Require abs(global HOURGLASS ENERGY)<=1e-3*max(abs(global INTERNAL ENERGY),1e-12 in that total-energy unit). For free quasi-static coupons separately require abs(global KINETIC ENERGY)/abs(global INTERNAL ENERGY)<=.01 outside a frozen initial ramp mask with abs(global INTERNAL ENERGY)>1e-12 total-energy unit; an unmasked sample below that denominator floor is diagnostic not_assessed, never silently zero. Also require force imbalance<=1%. BRIC IE is the separate source/D-profile energy-density observable and is NOT a denominator for global total-energy ratios. Affine fully constrained tests do not certify the free-coupon criteria.

Ka1 exact timestep recipe: source-checked DTIX followed by initial/max seconds. Baseline2e-6/2e-6;half1e-6/1e-6;no mass scaling. freform reads DTIN/DTMX,rejects max<0 and initial>max,sets max0 huge/default; use no zero. Require actual observed dt<=.01*min(eta_n/(Eeff+Et),eta_s/(Eeff+Et)).
Frozen91comparison grid0..0.045s step.0005; linear interpolation from actual native TIME with a bracket at every sample,no extrapolation/endpoint invention. Refinement pointwise abs(base-half)<=1e-5MPa+.0025|half|. Third half-step only on declared failure; preserve failed prior bytes.

## 6. Orthotropy and explicitly derived XBP

Material1=MD,2=CD,3=MD cross CD. Work-conjugate s=(s11,s22,s12),e=(e11,e22,gamma12=2eps12).
S=[[1/E1,-nu12/E1,0],[-nu12/E1,1/E2,0],[0,0,1/G12]];
e=S*s;nu21=nu12*E2/E1 linked.
Require E1,E2,G12>0,1-nu12*nu21>1e-6. Plane stress out-of-plane stresses0; no ZD/bending/delamination/damage. Free r in[-.95,.95] links nu12=r*sqrt(E1/E2), preserving SPD while moduli vary. Do not free both Poisson ratios.

Explicit orthonormal Q with det+1 tolerance1e-10 rotates full tensors sigma_m=Q^T*sigma_g*Q and strains likewise, then converts engineering shear. Never rotate stress vectors as physical3-vectors. Acceptance includes +-45deg,90deg,roundtrip and factor2 shear.

Elastic tests: MD/CD tension with longitudinal+transverse gauges and direct shear or45deg tension with full strain tensor. Missing transverse data fixesnu12; missing shear-sensitive data fixesG12. Measured A0=b*t uncertainty is required; thickness/modulus otherwise confound. Force/A0 is nominal stress: only identify with Cauchy under explicit small-strain<=.01/negligible-area-change policy, else require actual area. Fixed RH/T/moisture defines a model revision, no moisture law inferred. Holdout22.5/67.5deg independent conditioned specimens,replicate split intact.

Elastic fixture E1=4000,E2=1000,G12=500MPa,nu12.25,nu21.0625. MDs11=4 gives e11.001,e22-.00025. CDs22=1 gives e22.001,e11-.0000625. tau1 gives gamma.002. Numerical analytic tolerance1e-12relative/1e-14strain. This is directional elasticity, not paper plasticity or3D9constant support.

XBP user-facing label: GMN2012-derived power-hardening XBP, plane stress, tensor-norm variant. Do not infer this must be the user's intended Xia. Original full/tanh27parameter descriptor is not_implemented with fulltext/source gates; the selected reduced candidate below is fully specified instead of stopping at name ambiguity.

Normals N1=(cosθ1,sinθ1,0),N2=(cosθ2,sinθ2,0),N3=(0,0,1),N4=-N1,N5=-N2,N6=-N3.
f=sum_alpha((max(Nalpha dot s,0)/Yalpha(kappa))^(2k))-1;
Yalpha=qalpha*(1e-6+kappa)^nalpha,alpha1..5,Y6=Y3.
e=S*s+ep (=e_elastic+ep) and dep=dlambda*gradient_s(f).
Operational kappa=sqrt(ep11^2+ep22^2+.5*gammap12^2),norm of CURRENT plastic tensor, NOT accumulated path length. Original article's plastic-vector shear interpretation is not claimed directly audited. Initial supported domain is monotonic loading and elastic unloading; reverse plasticity not verified.

Backward Euler unknowns(s11,s22,s12,ep11,ep22,gammap12,dlambda),7residuals:
Rstress=S*s+ep-e_target (3);
Rflow=ep-ep_n-dlambda*grad_s f(s,kappa(ep)) (3);
Rf=f (1).
Elastic trial C*(e_target-ep_n),f<=1e-10 keeps ep.
Production analytic Jacobian: derivative max(x,0)^m=0 atx<=0,m>=4; kappa gradient regularized1e-14 at0. Central finite-difference7x7 check uses dimensionless1e-6 and half-step derivative agreement1e-4. Scale stress bymax(q,1MPa),strain by.01,multiplier bystressscale*.01. Newton35iterations,Armijo factor.5/max12/sufficientdecrease1e-4,dlambda>=0,Y>0. Compatibility and flow infinity-norm<=1e-10strain,|f|<=1e-8. Failure bisects strain increment max10levels,commits only after all substeps converge; store substep plan/hash. Failure CONSTITUTIVE_NONCONVERGENCE never returns trial stress as success.

State ep3+stress3+acceptedcontrol/time; no damage/moisture/finite-rotation state. Trapezoidal work and s:dep require plastic work>=-1e-9MPa within supported domain. Elastic unload keeps ep. Detect reverse plasticity via dep dot ep_n<-1e-12 and return UNSUPPORTED_STRESS_PATH instead of changing hardening to accumulated strain. Controlled-stress paths solve required free strains around the SAME update with consistent implicit tangent,outerNewton30,stress residual1e-8MPa+1e-8stressscale; each trial starts from prior committed state.

qalpha>0,n[0,.6],k fixed integer2(default)or3,unit normals and abs(sin(θ2-θ1))>=.2,Y3/Y6 linked. First directional recipe fixesθ1=0,θ2=90. Full demonstration optional boundsθ1[-30,0],θ2[90,120] keep normals separated. Compression q4/q5,n4/n5 remain fixed/unidentified unless independent buckling-free compression data exists; tension-only cruciform evidence cannot identify them.

Perfect-plastic fixture k2,N axes/opposites,q1=q4=4,q2=q5=2,q3=q6=1MPa,n0 gives yields MD4/CD2/shear1/equibiaxial1.9699162420218093MPa. Traction-free MD end e11.002,s11=4,ep11.001,e22=-nu12*s11/E1.
Power hardening independent scalar roots n.2:
MD E4000,q50,endpoint e.008: sigma16.473788317304816MPa,ep.003881552920673796;
shear G500,q10,endpoint gamma.02: tau3.869162563705432MPa,gammap.012261674872589137,kappa.008670313451112474.
MD scalar equation sigma=q*(1e-6+e-sigma/E)^n. Shear equation tau=q*(1e-6+abs(gamma-tau/G)/sqrt2)^n. Exact45deg tensor rotation supplies a third independent oracle including plastic shear.
General return-map limits stress1e-6MPa,kappa1e-8;perfect-plastic stress1e-8,ep1e-10;step-halving difference<=1e-4relative on MD/shear/proportionalbiaxial. Also gradientFD,axisrotation,rollback,stateisolation,unsupportedreverse tests. General7unknown kernel has NOT been executed in this task.

Material-point API is evaluate_path(parameters,control_history,initial_state)->PredictionBundle inside registered adapter callback. Three in-plane control components each have exactly one stress/strain choice and mandatory time knots. Output actual time,material/global stress3/strain3,ep3,kappa,Yalpha,work,residual/substep status. Homogeneous material paths are not cruciform DIC FE fields. No paid Abaqus dependency. Native solver export/Apply for this derived candidate=false; equation/parameter/orientation report does not masquerade as LAW32/43.

Identification: elastic parameters first,then tensile MD/CD/shear andbiaxial1:1/2:1 with transverse plastic strains for selected q1/q2/q3,n1/n2/n3 and optional θ. Demo q1[2,8],q2[1,4],q3[.5,2]MPa,n[0,.4]; these are study bounds,not paper universal defaults. Holdout22.5/67.5deg andbiaxial3:1. Original fullXBP27parameter fit,reverse plasticity andspecimenFEinverse fit remain unsupported by this backend.

## 7. Actual reference evidence and limits

Historical official Linux release latest-20261011 declared solver revision cb760c was executed by D on A-authored original Ka0/Ka1 raw decks. Starter0errors/0warnings,EngineNORMAL,TH converter0. Each CSV100rows x82columns; Ka0 last TIME.04950070,Ka1 .04950116,not assumed.05. Raw original hashes:
Ka0 CSV a86ddfe3c6af75de48b6359177e8ce08fb8d8b6ebc1cf2aa0291e249f9956012;
Ka1 CSV bd01598f59a3200d6eb3136045e2736e9fb6c73c326958c1d66c5ef27ed715b2.
A independent fine1D source-law integration maxstress error2.708214999e-7/3.123068068e-6MPa;F separately used DOP853 and obtained2.70927677e-7/3.37898440e-6MPa. F checked all8node U,J(rho/VOL/nodal),other5stress,addedmass/HG;maxU~1.01e-8mm,maxJ~5.10e-7,addedmass/HG0. These pass fixed mixed tolerances only for the short affine law probes.

A separately executed DTIX cap/half-cap pair; do not retroactively change original uncapped receipts:
baseline CSV SHA256 92f76f9076b0a387a82deab460165b59efdaaa0ef368e483f2631e0c6bf8836f;
half CSV e04b55417e9372ba7b062fc529cbbeb48780fc511af68ef316ef5c44482bae4e.
Both actual TIME STEP caps and Engine listing confirm2e-6/1e-6. Both cover0..0.0495; frozen91grid only0..0.045. Max baseline-half7.56674e-6MPa,A baseline-oracle5.20662e-6,half-oracle7.56674e-6;F independent baseline5.21684e-6,half7.56674e-6. All samplewise mixed tolerances pass. Reconstructed code below is not claimed a new execution in the unavailable environment.

Exact official binary SHA256 used:
starter_linux64_gf=0bbea37273656b270bea53cf756b115bcd5d9a8875a3c41283922a3b927719d1;
engine_linux64_gf=54cc9fc2a7cea6f1215afb6da9109d368650ff1fa7985625cb111f47d7e92be4;
th_to_csv_linux64_gf=704d647331ae4befd329d18b991d644f2af930abd117c5d204c23925f75fc8db.
Source-build identity of Tools is a different statement from release binary identity.

The original package had portable explicit --opencourant-root/--output-root runner with binary hash checks and --native-root verifier. It was actually rerun in a new output location and reproduced both cap CSV hashes. Eight stdlib arithmetic/archived-output scripts were copied to a different complete evidence layout and passed without original checkout/native binary imports. Historical receipts' absolute argv paths are observations, not future required locations. Recovery cannot reassert original receipt byte hashes without the bytes.

New implementation tests: test_material_dp_axisym.py,test_geos_dp_history.py,test_material_foam33.py,test_material_orthotropic.py,test_material_xbp.py plus shared material contract/evaluator/service tests. Registered CLI python -m caelab.materials validate|evaluate|fit|inspect --study <manifest> consumes data,not arbitrary import code. Full user acceptance is independent CSV->configure->evaluate/fit->holdout->inspect residuals->save/reopen->honest export/Apply status with cancel/error and actual Windows GUI.

No completion inflation: exact source research, arithmetic/limit fixtures,shortLinuxLAW33 andcaprefinement are real limited evidence. GEOS/Linux native,generalXBPkernel,fullKa1longhold/rate/shear,independentreal-materialexperiments,newproductservice/provider/WindowsUI remain unexecuted/unimplemented. All4calibrationlayers(invocation,faithfulhistorytransfer,namedlawcomputation,userworkflow)report separately.

## 8. New-model onboarding gate

A future model is not supported just because metadata registers. Require:
1 exact law/version/source/license/equation locations;
2 parameters/state/units/measure/frame/shear/sign and domain;
3 actual installed analytic/library/native backend and every API/cardbinding;
4 controls/required observables/extractor/coverage with independently checked raw fixture;
5 initialstate/reset/history/numerical convergence and unsupported regions;
6 experimental conversion/splits/weights/scales/identifiability;
7 independent limit/numerical oracle and physical assessment policy;
8 real export/Apply boundary;
9 trusted installed adapter/schema registration,never studyJSON moduleexecution;
10 platform/native tests when claimed and shared service end-to-end acceptance.
Missing gates keep that capability declared/unverified orunsupported with reasons;they do not block unrelated original native-deck execution.
