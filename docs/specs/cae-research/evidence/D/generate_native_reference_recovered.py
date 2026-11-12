"""Reconstructed standalone reference-deck generator; never runs a solver.
This is a new recovery artifact, not proof of byte identity with the lost generator.
"""
from pathlib import Path
import argparse

def render(name, layered):
    f = lambda x: f"{x:20.12g}"
    i = lambda x: f"{x:10d}"
    nodes = {11:(0,0,0),12:(10,0,0),13:(10,10,0),14:(0,10,0),
             15:(0,0,10),16:(10,0,10),17:(10,10,10),18:(0,10,10),
             21:(0,0,20),22:(10,0,20),23:(10,10,20),24:(0,10,20)}
    s = ["#RADIOSS STARTER","/BEGIN",name,i(2024),
         "                  Mg                  mm                   s",
         "                  Mg                  mm                   s",
         "/MAT/LAW2/1" if layered else "/MAT/LAW1/1","elastic",f(7.8e-9)]
    if layered:
        s += [f(1000)+f(.25)+i(0),
              "".join(f(x) for x in [1000,1,1,1e30,1e30]),
              f(0)+f(1)+i(0)+i(0)+f(0)+f(0),
              "".join(f(x) for x in [1,1e30,1,0])]
    else:
        s += [f(1000)+f(.25)]
    s += ["/PROP/SOLID/1","solid",
          "".join(i(x) for x in [1,4,0,0,0,0,1000,1])+f(0),
          "".join(f(x) for x in [1.1,.05,.1,0,0]),
          "".join(f(0) for _ in range(5)),
          "/PROP/SHELL/2","shell",
          "".join(i(x) for x in [24,4,2,2,0])+" "*10+f(0),
          "".join(f(x) for x in [0,0,0,0,.015]),
          i(5 if layered else 0)+" "*10+f(1)+f(5/6)+" "*10+i(2)+i(0)+i(0),
          "/PART/10","hex",i(1)+i(1)+i(0),
          "/PART/20","quad",i(2)+i(1)+i(0),"/NODE"]
    s += [i(k)+"".join(f(x) for x in xyz) for k,xyz in nodes.items()]
    s += ["/BRICK/10","".join(i(x) for x in [101,11,12,13,14,15,16,17,18]),
          "/SHELL/20","".join(i(x) for x in [202,21,22,23,24])]
    groups = [(1,[k for k,v in nodes.items() if v[0]==0]),
              (2,[k for k,v in nodes.items() if v[0]==10]),
              (3,list(nodes)),(4,[21,22,23,24])]
    for gid, members in groups:
        s += [f"/GRNOD/NODE/{gid}",f"G{gid}"]
        s += ["".join(i(x) for x in members[n:n+10]) for n in range(0,len(members),10)]
    s += ["/BCS/1","yz fixed","   011 000"+i(0)+i(3),
          "/BCS/2","shell rotation","   000 111"+i(0)+i(4),
          "/BCS/3","left x fixed","   100 000"+i(0)+i(1),
          "/FUNCT/1","constant",f(0)+f(1),f(.01)+f(1),
          "/IMPVEL/2","vx1",i(1)+f"{'X':>10}"+i(0)+i(0)+i(2)+i(0)+i(0),
          "".join(f(x) for x in [1,1,0,.01]),
          "/INIVEL/TRA/1","affine initial",f(1)+f(0)+f(0)+i(2)+i(0),
          "/TH/BRIC/1","BRIC",
          "".join(f"{x:<10}" for x in ["SX","SY","SZ","SXY","SYZ","SXZ","DENS","VOL","IE","BULK"]),
          i(101)+" "*10+"hex",
          "/TH/NODE/2","NODE",
          "".join(f"{x:<10}" for x in ["DX","DY","DZ","VX","VY","VZ"])]
    s += [i(k)+i(0)+f"n{k}" for k in nodes]
    s += ["/END"]
    engine = ["#RADIOSS ENGINE",f"/RUN/{name}/1",".01",
              "/ANIM/DT","0 .0025","/ANIM/VECT/DISP","/ANIM/VECT/VEL",
              "/ANIM/BRICK/TENS/STRESS/000","/ANIM/BRICK/TENS/STRAIN/000",
              "/ANIM/SHELL/TENS/STRESS/ALL","/ANIM/SHELL/TENS/STRAIN/ALL",
              "/ANIM/SHELL/TENS/STRESS/UPPER","/ANIM/SHELL/TENS/STRESS/LOWER",
              "/ANIM/SHELL/TENS/STRAIN/UPPER","/ANIM/SHELL/TENS/STRAIN/LOWER",
              "/TFILE/4",".0025","/TH/TITLE"]
    return "\n".join(s)+"\n", "\n".join(engine)+"\n"

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,required=True)
    args = parser.parse_args()
    for name,layered in [("affine",False),("layered",True)]:
        target=args.output/name
        target.mkdir(parents=True,exist_ok=True)
        starter,engine=render(name,layered)
        for suffix,text in [("0000",starter),("0001",engine)]:
            path=target/f"{name}_{suffix}.rad"
            with path.open("x",encoding="ascii",newline="\n") as out:
                out.write(text)
            print(path)

if __name__=="__main__":
    main()
