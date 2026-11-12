"""NEW RECONSTRUCTED fixed-profile native checker, unexecuted after environment loss.
Requires real archived CSV inputs. Never creates guessed native observations.
"""
from pathlib import Path
import argparse,csv,json,math,bisect,hashlib,re
p=argparse.ArgumentParser();p.add_argument("--native-root",type=Path,required=True);p.add_argument("--output",type=Path,required=True);args=p.parse_args();P=args.native_root.resolve()
knots=[(0,0),(.001,-1),(.019,-1),(.020,0),(.021,1),(.029,1),(.030,0),(.031,-1),(.039,-1),(.040,0),(.05,0)]
def control(t):
 dx=0.
 for (a,va),(b,vb) in zip(knots,knots[1:]):
  if t>=b:dx+=(va+vb)*.5*(b-a)
  else:
   u=max(0.,t-a);m=(vb-va)/(b-a);return dx+va*u+.5*m*u*u,va+m*u
 return dx,0.
def read(name,cap):
 d=P/name;path=d/"foam_ka1T01.csv"
 with path.open(newline="") as f:
  rr=csv.reader(f);head=[" ".join(x.split()) for x in next(rr)];rows=[[float(x) for x in r] for r in rr]
 assert len(head)==82 and len(set(head))==82
 assert len(rows)==100 and all(len(r)==82 and all(math.isfinite(v) for v in r) for r in rows)
 ix={s:i for i,s in enumerate(head)};ts=[r[ix["time"]] for r in rows]
 assert all(b>a for a,b in zip(ts,ts[1:]))
 assert max(r[ix["TIME STEP"]] for r in rows)<=cap*(1+1e-6)
 listing=(d/"foam_ka1_0001.out").read_text(errors="replace")
 assert "NORMAL TERMINATION" in listing
 for label in ("INITIAL TIME STEP","MAXIMUM TIME STEP"):
  line=next(z for z in listing.splitlines() if label in z)
  val=float(re.findall(r"[-+]?\d+\.\d+[Ee][-+]?\d+",line)[-1])
  assert abs(val-cap)<cap*1e-6
 for r,t in zip(rows,ts):
  dx,v=control(t);J=1+dx
  for n in range(1,9):
   target=dx if n in (2,3,6,7) else 0.
   assert abs(r[ix[f"NODE {n} n{n} DX"]]-target)<=1e-7+1e-4*abs(target)
   assert abs(r[ix[f"NODE {n} n{n} DY"]])<1e-7
   assert abs(r[ix[f"NODE {n} n{n} DZ"]])<1e-7
  assert abs(r[ix["BRIC 1 foam VOL"]]-J)<1e-5
  assert abs(1e-10/r[ix["BRIC 1 foam DENS"]]-J)<1e-5
  assert abs(r[ix["ADDED MASS"]])<1e-18 and abs(r[ix["HOURGLASS ENERGY"]])<1e-12
  for comp in ("SY","SZ","SXY","SYZ","SXZ"):assert abs(r[ix["BRIC 1 foam "+comp]])<1e-6
 # VX is leapfrog half-step: deliberately no current-TIME velocity assertion.
 return path,ts,[r[ix["BRIC 1 foam SX"]] for r in rows]
def interp(t,x,y):
 i=bisect.bisect_left(x,t)
 if i<len(x) and x[i]==t:return y[i]
 if i==0 or i==len(x):raise ValueError("COVERAGE_INCOMPLETE")
 w=(t-x[i-1])/(x[i]-x[i-1]);return y[i-1]+w*(y[i]-y[i-1])
base=read("ka1_dt2e6",2e-6);half=read("ka1_dt1e6",1e-6);grid=[.0005*i for i in range(91)]
refs=[];t=0.;sig=0.
for target in grid:
 while t<target-1e-16:
  dt=min(1.25e-7,target-t);x,v=control(t+.5*dt);lam=1+x;rate=v/lam;e=math.log(lam);E=max(10.,10.+.1*abs(rate))
  sig=max(-.1,min(.1,sig+dt*(E*rate-(E+2)/.5*sig+E*2/.5*e)));t+=dt
 refs.append(sig)
comparisons=[]
for t,oracle in zip(grid,refs):
 b=interp(t,base[1],base[2]);h=interp(t,half[1],half[2]);tol=1e-5+.005*abs(oracle);rtol=1e-5+.0025*abs(h)
 assert abs(b-oracle)<=tol and abs(h-oracle)<=tol and abs(b-h)<=rtol
 comparisons.append([t,b,h,oracle,abs(b-h),abs(b-oracle),abs(h-oracle)])
result={"kind":"new reconstructed checker result only after execution on actual CSV/listings","source_sha256":{str(v[0].relative_to(P)):hashlib.sha256(v[0].read_bytes()).hexdigest() for v in (base,half)},"grid":{"start":0,"stop":.045,"step":.0005,"count":91,"interpolation":"linear actual source TIME; bracket required; no extrapolation"},"oracle_tolerance_MPa":"1e-5+.005*abs(oracle)","refinement_tolerance_MPa":"1e-5+.0025*abs(half)","max_refinement_difference_MPa":max(r[4] for r in comparisons),"max_baseline_oracle_error_MPa":max(r[5] for r in comparisons),"max_half_oracle_error_MPa":max(r[6] for r in comparisons),"all_checks_pass":True,"rows":comparisons,"product_workflow":False}
args.output.write_text(json.dumps(result,indent=2)+"\n");print("PASS new fixed-profile native assessment")
