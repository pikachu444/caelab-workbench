"""NEW RECONSTRUCTED A reference generators after environment loss.
Equivalent numerical recipes were exercised historically; this recovered source
has not been executed in the unavailable environment. All generated observations
are synthetic, never recovered native CSV or experimental evidence.
"""
from pathlib import Path
import argparse,csv,json,math,hashlib

parser=argparse.ArgumentParser()
parser.add_argument("--output-root",type=Path,required=True)
args=parser.parse_args()
P=args.output_root.resolve();P.mkdir(parents=True,exist_ok=True)
def js(name,obj): (P/name).write_text(json.dumps(obj,indent=2,allow_nan=False)+"\n")
def table(name,header,rows):
 with (P/name).open("w",newline="") as f:
  w=csv.writer(f);w.writerow(header);w.writerows(rows)

# Independent piecewise constant-confinement DP algebra.
K,G,c,phi,psi,h=1e9,6e8,1e6,30.,10.,5e7
E=9*K*G/(3*K+G);nu=(3*K-2*G)/(2*(3*K+G));p0=-2e6
F=6*math.sin(math.radians(phi))/(3-math.sin(math.radians(phi)))
D=6*math.sin(math.radians(psi))/(3-math.sin(math.radians(psi)))
a0=6*c*math.cos(math.radians(phi))/(3-math.sin(math.radians(phi)))
qy=(a0-F*p0)/(1-F/3);ey=-qy/E
Eep=1/(1/E+(D-3)*(F-3)/(9*h))
rows=[]
for i in range(101):
 t=i/100;ea=-.01*t
 if ea>=ey:q=-E*ea;er=-nu*ea
 else:
  ds=Eep*(ea-ey);dl=(F/3-1)*ds/h
  q=qy-ds;er=-nu*ey-nu*ds/E+dl*(D/3+.5)
 rows.append([t,ea,er,er,p0-q,p0,p0,-(3*p0-q)/3,q,ea+2*er])
header=["time_s","eps_axial","eps_radial1","eps_radial2","sig_axial_Pa","sig_radial1_Pa","sig_radial2_Pa","compression_pressure_Pa","q_Pa","eps_v"]
table("dp_synthetic_train.csv",header,rows)
js("dp_expected.json",{"synthetic":True,"native_executed":False,"kind":"independent piecewise mixed-control algebra","K_Pa":K,"G_Pa":G,"c_Pa":c,"phi_deg":phi,"psi_deg":psi,"h_Pa":h,"F":F,"D":D,"a0_Pa":a0,"yield_q_Pa":qy,"yield_eps_axial":ey,"Eep_Pa":Eep,"final":dict(zip(header,rows[-1]))})
# Independent exact incremental 2x2 check, no bracket trials.
sa=sr=p0;a=a0;er=0.;max_error=0
A=F/3-1;B=1+2*F/3;C=D/3-1
for row in rows[1:]:
 de=-.0001;dr=0.;T=E*de+2*nu*dr
 ft=A*(sa+T)+B*sr-a
 dl=max(0.,ft/(h+E*A*C));ds=T-E*C*dl
 er+=(-nu*ds+(1-nu)*dr)/E+dl*(.5+D/3);sa+=ds;a+=h*dl
 max_error=max(max_error,abs(sa-row[4]))
 assert abs(er-row[2])<1e-13
assert max_error<1e-6
js("dp_mixed_expected.json",{"synthetic":True,"max_stress_difference_Pa":max_error,"final_axial_Pa":sa,"final_radial_strain":er,"invalid_old_bracket_p_Pa":p0+K*(-.0001+2*.1)})
xml='''<?xml version="1.0"?>
<Problem>
 <Events maxTime="1"><SoloEvent name="driver" target="/Tasks/driver"/></Events>
 <Constitutive><DruckerPrager name="dp" defaultDensity="2700" defaultBulkModulus="1000000000.0" defaultShearModulus="600000000.0" defaultCohesion="1000000.0" defaultFrictionAngle="30" defaultDilationAngle="10" defaultHardeningRate="50000000.0"/></Constitutive>
 <Tasks><TriaxialDriver name="driver" material="dp" mode="mixedControl" axialControl="axial" radialControl="radial" initialStress="-2000000.0" steps="100" precision="16" output="dp_native.txt"/></Tasks>
 <Functions><TableFunction name="axial" inputVarNames="{ time }" coordinates="{0,1}" values="{0,-0.01}"/><TableFunction name="radial" inputVarNames="{ time }" coordinates="{0,1}" values="{-2000000.0,-2000000.0}"/></Functions>
</Problem>
'''
(P/"dp_geos.xml").write_text(xml)
# Ka0 principal clipping limit driven by exact log increments.
foam=[];sig=0.;prev=0.
for i in range(61):
 t=i/60;e=-.02*i/20 if i<=20 else -.02+.01*(i-20)/20 if i<=40 else -.01-.01*(i-40)/20
 sig=max(-.1,min(.1,sig+10*(e-prev)));prev=e
 foam.append([t,e,math.exp(e),math.exp(e)-1,sig,0.,0.])
table("foam_ka0_synthetic.csv",["time_s","eps_log_x","J","gamma","sxx_MPa","syy_MPa","szz_MPa"],foam)
js("foam_expected.json",{"synthetic":True,"native_executed":False,"at_max_compression":foam[20],"after_unload":foam[40],"after_reload":foam[60]})
assert abs(foam[20][4]+.1)<1e-12 and abs(foam[40][4])<1e-12
# Nonzero air source-order oracle, deliberately a kernel update rather than FE increment.
P0=.1;Phi=.1;Jold=.9;Jnew=.91
pa=lambda J:max(0.,-P0*(J-1)/(J-Phi))
oldair,newair=pa(Jold),pa(Jnew);oldtotal=-.1-oldair;deps=.002
trial=oldtotal+newair+10*deps
newtotal=max(-.1,min(.1,trial))-newair
wrong=max(-.1,min(.1,oldtotal+oldair+10*deps))-newair
assert abs(newtotal-wrong)>1e-4
js("foam_air_expected.json",{"synthetic":True,"kind":"single current-density source-order unit update, not consistent FE increment","P0_MPa":P0,"Phi":Phi,"J_old":Jold,"J_new":Jnew,"old_air_MPa":oldair,"new_air_MPa":newair,"old_total_MPa":oldtotal,"deps_x":deps,"expected_total_MPa":newtotal,"incorrect_previous_air_MPa":wrong})
# Exact integrating-factor Ka1 segment solution; cap inactive.
s0=eps0=t0=0.;visco=[]
for duration,rate in [(1.,-.02),(2.,0.),(1.,.02)]:
 Em=max(10.,.1*abs(rate)+10.);Et=2.;eta=.5;aa=(Em+Et)/eta;bb=Em*Et/eta
 const=Em*rate+bb*eps0
 for j in range(101):
  t=duration*j/100
  ss=s0*math.exp(-aa*t)+const/aa*(1-math.exp(-aa*t))+bb*rate*(t/aa-(1-math.exp(-aa*t))/aa**2)
  ee=eps0+rate*t
  if not visco or j>0:visco.append([t0+t,ee,ss,rate,Em])
 s0,eps0,t0=ss,ee,t0+duration
assert max(abs(r[2]) for r in visco)<.1
table("foam_ka1_ode_exact.csv",["time_s","eps_total_x","sxx_MPa","rate_per_s","Eeff_MPa"],visco)
js("foam_ka1_expected.json",{"synthetic":True,"native_executed":False,"kind":"independent exact linear ODE","end_loading":visco[100],"end_hold":visco[200],"end_unloading":visco[-1]})
# Independently authored native fragments and full reference pairs.
f=lambda x:f"{x:20.12g}"
ii=lambda x:f"{x:10d}"
coords=[(0,0,0),(1,0,0),(1,1,0),(0,1,0),(0,0,1),(1,0,1),(1,1,1),(0,1,1)]
vel=[(0,0),(.001,-1),(.019,-1),(.020,0),(.021,1),(.029,1),(.030,0),(.031,-1),(.039,-1),(.040,0),(.05,0)]
for ka in (0,1):
 name=f"foam_ka{ka}"
 mat=["/MAT/LAW33/1","A synthetic foam; native verification pending",f(1e-10),f(10)+ii(ka)+ii(0)+f(1),"".join(f(x) for x in [0,.1,0]),"".join(f(x) for x in [.1,0,0,100])]
 if ka:mat+=["".join(f(x) for x in [.1,10,2,.5,.5])]
 (P/f"{name}_material.rad").write_text("\n".join(mat)+"\n")
 ss=["#RADIOSS STARTER","/BEGIN",name,ii(2024),"                  Mg                  mm                   s","                  Mg                  mm                   s"]+mat
 ss+=["/PROP/SOLID/1","affine one-point small-strain reference","".join(ii(x) for x in [1,4,0,0,0,0,1000,1])+f(0),"".join(f(0) for _ in range(5)),"".join(f(0) for _ in range(5)),"/PART/1","foam",ii(1)+ii(1)+ii(0),"/NODE"]
 ss+=[ii(k)+"".join(f(x) for x in xyz) for k,xyz in enumerate(coords,1)]
 ss+=["/BRICK/1","".join(ii(x) for x in [1,1,2,3,4,5,6,7,8])]
 for gid,ns in [(1,[1,4,5,8]),(2,[2,3,6,7]),(3,list(range(1,9)))]:
  ss+=[f"/GRNOD/NODE/{gid}",f"group{gid}","".join(ii(k) for k in ns)]
 ss+=["/BCS/1","all yz zero","   011 000"+ii(0)+ii(3),"/BCS/2","left x zero","   100 000"+ii(0)+ii(1),"/FUNCT/1","velocity with smooth ramps"]
 ss+=[f(t)+f(v) for t,v in vel]
 ss+=["/IMPVEL/1","right vx",ii(1)+f'{"X":>10}'+ii(0)+ii(0)+ii(2)+ii(0)+ii(0),"".join(f(x) for x in [1,1,0,.05])]
 ss+=["/TH/BRIC/1","BRIC","".join(f"{x:<10}" for x in ["SX","SY","SZ","SXY","SYZ","SXZ","DENS","VOL","IE","BULK"]),ii(1)+" "*10+"foam"]
 ss+=["/TH/NODE/2","NODE","".join(f"{x:<10}" for x in ["DX","DY","DZ","VX","VY","VZ"])]
 ss+=[ii(k)+ii(0)+f"n{k}" for k in range(1,9)]
 ss+=["/END"]
 (P/f"{name}_0000.rad").write_text("\n".join(ss)+"\n")
 eng=["#RADIOSS ENGINE",f"/RUN/{name}/1",".05","/TFILE/4",".0005","/TH/TITLE"]
 (P/f"{name}_0001.rad").write_text("\n".join(eng)+"\n")
 if ka:
  for label,dt in [("baseline",2e-6),("half",1e-6)]:
   (P/f"foam_ka1_{label}_engine.rad").write_text("\n".join(eng)+f"\n/DTIX\n{dt:.12g} {dt:.12g}\n")
# Orthotropic and XBP independent limits.
E1,E2,G12,nu12=4000.,1000.,500.,.25;nu21=nu12*E2/E1
table("orthotropic_elastic_expected.csv",["case","s11_MPa","s22_MPa","s12_MPa","e11","e22","gamma12"],[["MD",4,0,0,.001,-.00025,0],["CD",0,1,0,-.0000625,.001,0],["SHEAR",0,0,1,0,0,.002]])
assert 1-nu12*nu21>0
def scalar(e,M,q,n,shear=False):
 if M*e<=q*1e-6**n:return M*e,0.
 lo,hi=0.,M*e
 for _ in range(100):
  s=(lo+hi)/2;ep=e-s/M;k=abs(ep)/(math.sqrt(2) if shear else 1)
  if s>q*(1e-6+k)**n:hi=s
  else:lo=s
 s=(lo+hi)/2;return s,e-s/M
md=[];sh=[];rot=[];perfect=[]
for i in range(81):
 e=.008*i/80;s,ep=scalar(e,4000,50,.2);e2=-.25*s/4000
 md.append([i/80,e,e2,s,0.,0.,ep,0.,0.,ep])
 rot.append([i/80,.5*(e+e2),.5*(e+e2),e-e2,.5*s,.5*s,.5*s,.5*ep,.5*ep,ep])
 gamma=.02*i/80;tau,gp=scalar(gamma,500,10,.2,True)
 sh.append([i/80,gamma,tau,gp,abs(gp)/math.sqrt(2)])
for i in range(21):
 e=.0001*i;s=min(E1*e,4);ep=max(0,e-s/E1);perfect.append([i/20,e,-nu12*s/E1,s,0.,0.,ep])
table("xbp_hardening_md.csv",["time","e11","e22","s11_MPa","s22_MPa","s12_MPa","ep11","ep22","gammap12","kappa"],md)
table("xbp_hardening_shear.csv",["time","gamma12","tau12_MPa","gammap12","kappa"],sh)
table("xbp_hardening_rot45.csv",["time","exx","eyy","gamma_xy","sxx_MPa","syy_MPa","sxy_MPa","epxx","epyy","gammap_xy"],rot)
table("xbp_perfect_plastic_limit.csv",["time_s","e11","e22","s11_MPa","s22_MPa","s12_MPa","ep11"],perfect)
assert abs(md[-1][3]-50*(1e-6+md[-1][-1])**.2)<1e-12
assert abs(sh[-1][2]-10*(1e-6+sh[-1][-1])**.2)<1e-12
js("xbp_hardening_expected.json",{"synthetic":True,"general_return_map_executed":False,"normal":{"E_MPa":4000,"q_MPa":50,"n":.2,"final":md[-1]},"shear":{"G_MPa":500,"q_MPa":10,"n":.2,"final":sh[-1]},"rotation45_final":rot[-1]})
js("orthotropic_xbp_expected.json",{"synthetic":True,"nu21":nu21,"SPD_minor":1-nu12*nu21,"k":2,"norm":"current plastic tensor norm, not accumulated arc length","power_exponents":[0]*6,"strengths_MPa":[4,2,1,4,2,1],"equibiaxial_yield_MPa":(4**-4+2**-4)**(-.25)})
files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in P.iterdir() if p.is_file() and p.name!="generated_manifest.json"}
js("generated_manifest.json",{"reconstructed_generator":True,"synthetic_fixtures":True,"native_program_executed_by_this_generator":False,"product_runtime_executed":False,"files":files})
print("PASS reconstructed reference arithmetic; no native program/general XBP kernel executed")
