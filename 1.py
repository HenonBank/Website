f = open('17_10.txt', mode = 'r')
spisok = []
min_special = 200000 

for i in range (-100000,100000): 
  polozhit_i = abs(i)
  if polozhit_i >= 100 and polozhit_i <= 999 and polozhit_i % 100 == 15:
      if i < min_special:
          min_special = i
            
kvadrat = min_special * min_special

kolichestvo = 0
min_proizvedenie = 20000000000  

vsego_chisel = len(spisok)

for i in range(vsego_chisel - 2):

    chislo1 = spisok[i]
    chislo2 = spisok[i+1]
    chislo3 = spisok[i+2]
    
    vse_plus = (chislo1 > 0 and chislo2 > 0 and chislo3 > 0)
    vse_minus = (chislo1 < 0 and chislo2 < 0 and chislo3 < 0)
    
    if vse_plus or vse_minus:
    
        troyka_min = min(chislo1, chislo2, chislo3)
        troyka_max = max(chislo1, chislo2, chislo3)
        
        proizvedenie = troyka_min * troyka_max
        
        if proizvedenie > kvadrat:
            kolichestvo += 1
            
            if proizvedenie < min_proizvedenie:
                min_proizvedenie = proizvedenie

print(kolichestvo)
print(min_proizvedenie)
