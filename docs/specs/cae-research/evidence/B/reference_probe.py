"""Reconstructed independent synthetic material reference generator.
No product constitutive library and no independent measured physical data.
Run actual pyvisco/NEML/core probes separately. Reconstructed code must be rerun
and reviewed; earlier recorded outputs do not attest these new bytes.
"""
from pathlib import Path
import csv,json,math,platform
import numpy as np
from scipy.optimize import brentq,least_squares
from equilibrium_probe import terms,select_unique
ROOT=Path(__file__).resolve().parent
def write_csv(name,names,rows):
    with (ROOT/name).open('w',newline='') as f:
        w=csv.writer(f);w.writerow(names);w.writerows(rows)
def ogden_nominal(lam,mode,mu=(1.,),alpha=(2.,)):
    s={'uniaxial':(lam,lam**-.5,lam**-.5),
       'biaxial':(lam,lam,lam**-2),'planar':(lam,1.,lam**-1)}[mode]
    beta=np.sum([2*m/a*np.array(s)**a for m,a in zip(mu,alpha)],axis=0)
    return float((beta[0]-beta[2])/lam)
def comp_stress(stretches,mu=(1.,),alpha=(2.,),bulk=100.):
    s=np.asarray(stretches,dtype=float);J=np.prod(s);bar=s*J**(-1/3)
    beta=np.sum([2*m/a*bar**a for m,a in zip(mu,alpha)],axis=0)
    return (beta-beta.mean())/J+bulk*(J-1)
def comp_energy(F,mu=(1.,),alpha=(2.,),bulk=100.):
    J=np.linalg.det(F);s=np.linalg.svd(F,compute_uv=False)*J**(-1/3)
    return sum(2*m/a**2*(np.sum(s**a)-3) for m,a in zip(mu,alpha))+bulk/2*(J-1)**2
def comp_tensor(F):
    u,s,v=np.linalg.svd(F);sig=u@np.diag(comp_stress(s))@u.T
    return sig,np.linalg.det(F)*sig@np.linalg.inv(F).T
def wlf(T,Tref=293.15,C1=5.,C2=100.):
    return 10**(-C1*(T-Tref)/(C2+T-Tref))
def relax(t,E0=100.,fractions=(.2,.3),tau=(.1,10.)):
    return E0*(1-sum(fractions)+np.dot(fractions,np.exp(-t/np.array(tau))))
def dma(omega,E0=100.,fractions=(.2,.3),tau=(.1,10.)):
    z=omega*np.array(tau)
    return (E0*(1-sum(fractions)+np.dot(fractions,z*z/(1+z*z))),
            E0*np.dot(fractions,z/(1+z*z)))
def maxwell_ramp(deps,dt,T,q,E0=100.,fractions=(.2,.3),tau=(.1,10.)):
    r=dt/wlf(T)/np.array(tau)
    return np.asarray(q)*np.exp(-r)+E0*np.asarray(fractions)*deps*(-np.expm1(-r)/r)
PAR=dict(E=200000.,sy=250.,Q=50.,b=10.,C=np.array([20000.,5000.]),gamma=np.array([200.,20.]))
def cyclic_step(eps,ep,p,X,par=PAR):
    E,sy,Q,b,C,g=(par[k] for k in ('E','sy','Q','b','C','gamma'))
    trial=E*(eps-ep);direction=1. if trial-X.sum()>=0 else -1.
    def f(dp):
        xx=(X+C*direction*dp)/(1+g*dp)
        return direction*(trial-E*direction*dp-xx.sum())-sy-Q*(-np.expm1(-b*(p+dp)))
    if f(0)<=1e-10:return trial,ep,p,X.copy()
    hi=max(abs(eps-ep)*2,1e-8)
    while f(hi)>0:hi*=2
    dp=brentq(f,0.,hi,xtol=1e-15)
    xx=(X+C*direction*dp)/(1+g*dp);ep+=direction*dp
    return E*(eps-ep),ep,p+dp,xx
def cyclic_history(vertices,increment=1e-5):
    ep=p=t=last=0.;X=np.zeros(2);rows=[(0.,0.,0.,0.,0.,0.,0.)]
    for vertex in vertices:
        n=max(1,math.ceil(abs(vertex-last)/increment))
        for eps in np.linspace(last,vertex,n+1)[1:]:
            t+=abs(eps-rows[-1][1])/.001
            sig,ep,p,X=cyclic_step(eps,ep,p,X)
            rows.append((t,eps,sig,ep,p,*X))
        last=vertex
    return np.array(rows)
def stress_history(vertices,step=.5):
    ep=p=t=last=0.;X=np.zeros(2);rows=[(0.,0.,0.,0.,0.)]
    for vertex in vertices:
        n=max(1,math.ceil(abs(vertex-last)/step))
        for sig in np.linspace(last,vertex,n+1)[1:]:
            direction=1. if sig-X.sum()>=0 else -1.
            def f(dp):
                xx=(X+PAR['C']*direction*dp)/(1+PAR['gamma']*dp)
                return direction*(sig-xx.sum())-PAR['sy']-PAR['Q']*(-np.expm1(-PAR['b']*(p+dp)))
            if f(0)>1e-10:
                hi=1e-5
                while f(hi)>0 and hi<1.:hi*=2
                if f(hi)>0:raise ValueError('STRESS_CONTROL_NO_EQUILIBRIUM')
                dp=brentq(f,0.,hi,xtol=1e-15)
                X=(X+PAR['C']*direction*dp)/(1+PAR['gamma']*dp)
                ep+=direction*dp;p+=dp
            t+=abs(sig-last)/100.
            rows.append((t,sig,sig/PAR['E']+ep,ep,p));last=sig
    return np.array(rows)
def jc_flow(p,rate,T,A=300.,B=400.,n=.5,C=.02,m=1.,
            rate0=1.,Tr=293.15,Tm=1293.15):
    theta=(T-Tr)/(Tm-Tr)
    return (A+B*p**n)*(1+C*np.log(max(1.,rate/rate0)))*(1-theta**m)
def jc_failure(eta,rate,T,D1=.1,D2=.2,D3=-1.,D4=.01,D5=.2,
               rate0=1.,Tr=293.15,Tm=1293.15,emin=1e-6):
    theta=(T-Tr)/(Tm-Tr)
    return max(emin,(D1+D2*np.exp(D3*eta))*(1+D4*np.log(max(1.,rate/rate0)))*(1+D5*theta))
def finite_jac(fn,x):
    cols=[]
    for i,v in enumerate(x):
        h=1e-5*max(abs(v),1.);a=np.array(x,dtype=float);b=a.copy()
        a[i]+=h;b[i]-=h
        cols.append((np.asarray(fn(a))-np.asarray(fn(b)))/(2*h))
    return np.array(cols).T
def main():
    checks={}
    for mode,want in [('uniaxial',1.75),('biaxial',1.96875),('planar',1.875)]:
        got=ogden_nominal(2.,mode);assert abs(got-want)<1e-13
        checks['HYP_'+mode]=got
    hydro=comp_stress(np.full(3,1.03**(1/3)))
    assert np.allclose(hydro,3.,atol=1e-12,rtol=0)
    checks['HYP_hydro_cauchy_MPa']=hydro.tolist()
    F=np.diag([.8,1.1,1.2]);R=np.array([[0.,-1.,0.],[1.,0.,0.],[0.,0.,1.]])
    sig,P=comp_tensor(F)
    assert np.allclose(comp_tensor(R@F)[0],R@sig@R.T,atol=1e-10,rtol=0)
    Pfd=np.zeros((3,3));h=1e-6
    for i in range(3):
        for j in range(3):
            d=np.zeros((3,3));d[i,j]=h
            Pfd[i,j]=(comp_energy(F+d)-comp_energy(F-d))/(2*h)
    checks['HYP_energy_gradient_max_error_MPa']=float(np.max(abs(P-Pfd)))
    assert checks['HYP_energy_gradient_max_error_MPa']<1e-6
    rows=[]
    for mode,split in [('uniaxial','train'),('biaxial','train'),('planar','holdout')]:
        for lam in np.linspace(.75,2.,26):
            rows.append((mode,split,lam,ogden_nominal(lam,mode,mu=(.8,.2),alpha=(1.3,5.))))
    write_csv('ogden.csv',['mode','split','stretch_1','nominal_stress_1_MPa'],rows)
    write_csv('volume.csv',['split','J','hydro_cauchy_stress_MPa'],
              [('train',J,100*(J-1)) for J in (.97,.99,1.,1.01,1.03)])
    comp=[]
    for lam in np.linspace(.75,2.,26):
        lateral,_=select_unique(terms('uniaxial',lam,[1.],[2.],100.))
        sigma=comp_stress([lam,lateral,lateral]);J=lam*lateral*lateral
        comp.append(('train',lam,lateral,J,J*sigma[0]/lam,sigma[1]))
    assert max(abs(x[-1]) for x in comp)<1e-9
    assert abs(comp[-1][-2]-ogden_nominal(2.,'uniaxial'))>1e-3
    write_csv('ogden_compressible.csv',['split','stretch_1','stretch_2_equal_3','J','nominal_stress_1_MPa','lateral_cauchy_MPa'],comp)
    cb=[]
    for lam in np.linspace(.85,1.85,21):
        z,_=select_unique(terms('biaxial',lam,[1.],[2.],100.))
        sigma=comp_stress([lam,lam,z]);J=lam*lam*z
        cb.append(('validation',lam,z,J,J*sigma[0]/lam,sigma[2]))
    write_csv('ogden_compressible_biax_validation.csv',['split','stretch_1_equal_2','stretch_3','J','nominal_stress_1_MPa','thickness_cauchy_MPa'],cb)
    checks['VIS_relax_0']=relax(0.);checks['VIS_relax_1']=relax(1.)
    checks['VIS_dma_omega_1']=dma(1.);checks['VIS_aT_313p15']=wlf(313.15)
    assert abs(relax(1.)-77.14603053967404)<1e-11
    assert np.allclose(dma(1.),(79.9009900990099,4.9504950495049505),atol=1e-12,rtol=0)
    assert abs(wlf(313.15)-10**(-5/6))<1e-14
    rows=[]
    for T,split in [(293.15,'train'),(303.15,'train'),(313.15,'train'),(318.15,'validation'),(323.15,'holdout')]:
        for f in np.logspace(-3,3,61):
            stor,loss=dma(2*np.pi*f*wlf(T));rows.append((split,T,f,stor,loss))
    write_csv('dma.csv',['split','temperature_K','frequency_Hz','storage_MPa','loss_MPa'],rows)
    write_csv('relax.csv',['split','temperature_K','time_s','relaxation_modulus_MPa'],
        [(s,T,t,relax(t/wlf(T))) for T,s in [(293.15,'train'),(313.15,'train'),(323.15,'holdout')]
         for t in np.logspace(-3,3,61)])
    q=maxwell_ramp(.01,1.,293.15,np.zeros(2))
    checks['VIS_ramp_branch_stress']=q.tolist()
    checks['VIS_ramp_total_stress']=.5+q.sum()
    X=100*(-np.expm1(-.1))
    checks['CYC_AF_backstress_at_p001']=X
    checks['CYC_AF_reverse_yield_MPa']=X-250.
    columns=['time_s','axial_total_strain','axial_cauchy_MPa','axial_plastic_strain','p',
             'backstress1_axial_equiv_MPa','backstress2_axial_equiv_MPa']
    vertices=[.006,-.006,.006,-.006,.006];cyc=cyclic_history(vertices)
    write_csv('cyclic.csv',columns,cyc)
    write_csv('cyclic_low.csv',columns,cyclic_history([.002,-.002,.002,-.002,.002]))
    write_csv('cyclic_validation.csv',columns,cyclic_history([.004,-.004,.004,-.004,.004]))
    refined=cyclic_history(vertices,5e-6)
    checks['CYC_final_coarse_MPa']=cyc[-1,2]
    checks['CYC_final_refined_MPa']=refined[-1,2]
    assert abs(cyc[-1,2]-refined[-1,2])<.05
    rat=stress_history([350.,-100.,350.,-100.,350.,-100.,350.])
    write_csv('ratcheting.csv',['time_s','axial_cauchy_MPa','axial_total_strain','axial_plastic_strain','p'],rat)
    checks['CYC_ratcheting_final_strain']=rat[-1,2]
    states=[('train',1.,293.15),('train',10.,293.15),('train',1.,793.15),
            ('validation',100.,493.15),('holdout',1000.,993.15)]
    write_csv('jc_flow.csv',['split','p','plastic_rate_per_s','temperature_K','flow_stress_MPa'],
        [(s,p,r,T,jc_flow(p,r,T)) for s,r,T in states for p in np.linspace(0,.2,21)])
    write_csv('jc_failure.csv',['split','triaxiality_tension_positive','plastic_rate_per_s','temperature_K','local_p_at_failure'],
        [(s,eta,r,T,jc_failure(eta,r,T)) for s,r,T in states for eta in (-1/3,0.,1/3,2/3)])
    checks['JC_flow_p01_r1_theta0']=jc_flow(.1,1.,293.15)
    checks['JC_failure_eta0_r1_theta0']=jc_failure(0.,1.,293.15)
    checks['JC_damage_after_p015']=.15/jc_failure(0.,1.,293.15)
    assert abs(checks['JC_flow_p01_r1_theta0']-426.4911064067352)<1e-10
    assert abs(checks['JC_damage_after_p015']-.5)<1e-14
    kj=finite_jac(lambda x:comp_stress([1.25,1.,.8],bulk=x[0]),[100.])
    checks['HYP_J1_K_sensitivity']=float(np.max(abs(kj)))
    wj=finite_jac(lambda p:[wlf(293.15,C1=p[0],C2=p[1])],[5.,100.])
    checks['VIS_Tref_only_shift_rank']=int(np.linalg.matrix_rank(wj,tol=1e-10))
    jj=finite_jac(lambda p:[jc_flow(e,1.,293.15,A=p[0],B=p[1],n=p[2],C=p[3],m=p[4])
                    for e in (.01,.02,.05,.1,.2)],[300,400,.5,.02,1.])
    checks['JC_one_rate_T_flow_rank']=int(np.linalg.matrix_rank(jj,tol=1e-8))
    dj=finite_jac(lambda p:[jc_failure(1/3,1.,293.15,D1=p[0],D2=p[1],D3=p[2],D4=p[3],D5=p[4])]*5,
                  [.1,.2,-1.,.01,.2])
    checks['JC_one_eta_rate_T_failure_rank']=int(np.linalg.matrix_rank(dj,tol=1e-8))
    assert checks['HYP_J1_K_sensitivity']<1e-12
    assert checks['VIS_Tref_only_shift_rank']==0
    assert checks['JC_one_rate_T_flow_rank']==3 and checks['JC_one_eta_rate_T_failure_rank']==1
    t=np.logspace(-3,3,61);obs=np.array([relax(v) for v in t])
    def pr(x):
        return np.array([relax(v,E0=x[0],fractions=x[1:3],tau=np.exp(x[3:5])) for v in t])
    fit=least_squares(lambda x:(pr(x)-obs)/100,[90,.18,.25,np.log(.2),np.log(8)],
         bounds=([1,1e-8,1e-8,np.log(.001),np.log(1)],[200,.45,.45,np.log(.9),np.log(1000)]),
         xtol=1e-13,ftol=1e-13,gtol=1e-13,max_nfev=1000)
    checks['VIS_synthetic_fit_loss']=float(np.mean(fit.fun**2))
    checks['VIS_synthetic_fit_physical']=[*fit.x[:3],*np.exp(fit.x[3:])]
    assert fit.success and checks['VIS_synthetic_fit_loss']<1e-18
    assert np.allclose(checks['VIS_synthetic_fit_physical'],[100,.2,.3,.1,10],atol=1e-6,rtol=0)
    out={'status':'passed','recovery_version':'B-RECOVERY-1',
         'scope':'newly generated synthetic arithmetic and independent scalar reference; not actual NEML/native/Windows',
         'python':platform.python_version(),'numpy':np.__version__,'checks':checks}
    (ROOT/'probe_receipt.json').write_text(json.dumps(out,indent=2)+'\n')
    print(json.dumps(out,indent=2))
if __name__=='__main__':main()
