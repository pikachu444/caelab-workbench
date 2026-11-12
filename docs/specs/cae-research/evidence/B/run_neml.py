"""Reconstructed actual NEML API comparison; source correspondence stays unverified."""
import csv,hashlib,importlib.metadata,json,os,sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parent
SHA='65ab2a648a975e2e9de8e1371be270567f9683ac'
def main():
    try:
        from neml import elasticity,hardening,surfaces,ri_flow,models,drivers
    except ImportError as exc:
        r={'status':'unavailable','code':'BACKEND_UNAVAILABLE','reason':str(exc),
           'library_executed':False,'scope':'no scalar substitute for actual NEML'}
        (ROOT/'neml_receipt.json').write_text(json.dumps(r,indent=2)+'\n')
        print(json.dumps(r));return 20
    def fresh():
        elastic=elasticity.IsotropicLinearElasticModel(200000.,'youngs',.3,'poissons')
        iso=hardening.VoceIsotropicHardeningRule(250.,50.,10.)
        kin=hardening.Chaboche(iso,[20000.,5000.],
             [hardening.ConstantGamma(200.),hardening.ConstantGamma(20.)],
             [0.,0.],[1.,1.],noniso=False)
        flow=ri_flow.RateIndependentNonAssociativeHardening(surfaces.IsoKinJ2(),kin)
        model=models.SmallStrainRateIndependentPlasticity(elastic,flow,
              rtol=1e-10,atol=1e-12,miter=100)
        return drivers.Driver_sd(model,T_init=293.15,no_thermal_strain=True,
                                  rtol=1e-9,atol=1e-11,miter=100)
    checks=[]
    for name in ['cyclic','cyclic_low','cyclic_validation','ratcheting']:
        data=np.genfromtxt(ROOT/(name+'.csv'),delimiter=',',names=True)
        d=fresh();out=[]
        for row in data[1:]:
            if name=='ratcheting':
                s=np.zeros(6);s[0]=row['axial_cauchy_MPa']
                d.stress_step(s,float(row['time_s']),293.15)
            else:
                inc=float(row['axial_total_strain'])-d.strain[-1,0]
                d.erate_einc_step(np.array([1.,0,0,0,0,0]),
                                  .001 if inc>0 else -.001,inc,293.15)
            out.append([d.t[-1],d.strain[-1,0],d.stress[-1,0]])
        arr=np.array(out);want=data[1:]
        se=float(np.max(abs(arr[:,2]-want['axial_cauchy_MPa'])))
        ee=float(np.max(abs(arr[:,1]-want['axial_total_strain'])))
        transverse=float(np.max(abs(d.stress[:,1:])))
        assert se<.002 and ee<1e-7 and transverse<1e-6,(name,se,ee,transverse)
        with (ROOT/(name+'_neml.csv')).open('w',newline='') as f:
            w=csv.writer(f);w.writerow(['time_s','axial_total_strain','axial_cauchy_MPa'])
            w.writerows(out)
        checks.append({'test':name,'max_stress_error_MPa':se,
                       'max_strain_error':ee,'max_transverse_MPa':transverse})
    r={'status':'numeric_pass_source_unverified','library_executed':True,
       'distribution_version':importlib.metadata.version('neml'),
       'requested_source_sha':SHA,'declared_build_source_sha':os.environ.get('NEML_SOURCE_SHA'),
       'source_correspondence':'unverified','build_receipt_ref':None,
       'loaded_extensions':[{'path_audit':m.__file__,
          'sha256':hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest()}
          for m in [elasticity,hardening,surfaces,ri_flow,models]],
       'checks':checks,'scope':'actual library/scalar reference, no source-build/Windows/product acceptance'}
    (ROOT/'neml_receipt.json').write_text(json.dumps(r,indent=2)+'\n')
    print(json.dumps(r,indent=2));return 21
if __name__=='__main__':sys.exit(main())
