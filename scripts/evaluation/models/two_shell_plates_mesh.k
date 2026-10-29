*KEYWORD
*TITLE
Two shell plates - mesh import test, mm-tonne-s
*PART
Lower plate
1,1,1,0,0,0,0,0
*PART
Upper plate
2,1,1,0,0,0,0,0
*SECTION_SHELL
1,2,0.833333,2,1,0,0,1
0.1,0.1,0.1,0.1
*MAT_ELASTIC
1,7.8e-9,210000,0.3
*NODE
1,0,0,0
2,10,0,0
3,10,10,0
4,0,10,0
5,0,0,1
6,10,0,1
7,10,10,1
8,0,10,1
*ELEMENT_SHELL
1,1,1,2,3,4
2,2,5,6,7,8
*END
