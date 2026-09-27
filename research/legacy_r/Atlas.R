# Para ejecutar esto deberase de colocar o ratón na línea 1 e cliquear en 'Run'
# hasta que se chegue ao final. Unha vez feito, deberase escribir o deseado no
# cuadro de abaixo, que ten flechas de este estilo: >

# Todas as medicións se farán en dólares($)

com = 0.0000000011 # Ganancia por parcela común por segundo
rar = 0.0000000016 # Ganancia por parcela rara por segundo
epi = 0.0000000022 # Ganancia por parcela épica por segundo
leg = 0.0000000044 # Ganancia por parcela legendaria por segundo


# Os cálculos presentados a continuación supóñense estáticos, é dicir, consideramos
# un número fixo de parcelas ao longo do tempo que queiramos. Só serve para cálculos
# puntuales, pero non serven en caso de querer estimar o crecemento ao longo do
# tempo, xa que se considera que compramos parcelas ao longo do tempo.

# Esta función calcula a ganancia en función do número de parcelas
# comúns (c), raras (r), épicas (e) e legendarias (l). Esta ganancia
# contabilízase segundo os días a considerar (d), o boost que aplique ao noso
# caso (b) e as horas diarias que consideremos implementar do boost anterior (h).
# Tamén considera a porcentaxe de beneficio extra por insignias (i), do seguinte xeito:
# 0      insignias -> 0%
# 1-10   insignias -> 5%
# 11-30  insignias -> 10%
# 31-60  insignias -> 15%
# 61-100 insignias -> 20%
# 101-   insignias -> 25%

ganancia = function(c,r,e,l,d,b,h,i){
(c * com + 
     r * rar +
     e * epi + 
     l * leg) * 60 * 60 * d * (1+i/100) * (b * h + (24-h))
}

# Exemplo para contabilizar 1 día (d = 1) con parcelas tales que c = 15, r = 12,
# e = 3, l = 1. Ademais, hai que ter en conta que se aplica o 'boost' de x20
# durante 22 de 24h tal que así:
ganancia(15,12,3,1,1,20,22,10)

# En caso de contabilizar 24h de forma total, en vez de 22 no último termo, colocaremos
# o valor de 24
ganancia(15,12,3,1,1, 20, 24,10)

# Se queremos contabilizar o que ganaríamos en 1 día no evento de SuperRentBoost,
# asumindo que estamos as 24h do día co 'boost':
ganancia(15,12,3,1,1,50,24,10)

# Se queremos contabilizar o que ganaríamos no equivalente dos 32 días que dura ao
# ano o evento, asumindo que estamos as 24h do día co 'boost'
ganancia(15,12,3,1,32,50,24,10)

# Imaginemos que non podemos estar as 24h do día no SuperRentBoost, só 22h:
ganancia(15,12,3,1,32,50,22,10)

# Todos estos exemplos son válidos para diferentes variacións de días (d), número
# de parcelas de cada tipo (c,r,e,l), multiplicador do boost (b) que se considere e
# horas consideradas ao día de 'boost' (h).

# Agora imaxinemos que queremos calcular o que se podería ganar en 1 ano natural.
# Deberemos de ter en conta que de 365 días totais, 32 son do evento SuperRentBoost
# e os restantes 333 días son de 'boost' normal x20. Para este caso consideraremos
# 22 de 24h nos días de 'boost' x20 y 24h de 'boost' x50:
ganancia(15,12,3,1,333,20,22,10) + ganancia(15,12,3,1,32,50,24,10)

#########################################################################
# TODOS OS VALORES AQUÍ EXPOSTOS SON INTERCAMBIABLES SEGUNDO NOS INTERESE
#########################################################################




########## FUNCIÓN QUE ESTIMA A GANANCIA CUN NÚMERO CONCRETO DE PARCELAS##########

# Supoñamos que queremos estimar a ganancia esperada cun número de parcelas que non
# temos aínda

# Para isto, empregaremos unha función similar á anterior, onde só teremos en conta
# o número de parcelas consideradas (p), os días a contabilizar (d), o boostaplicado
# (b), as horas diarias dese boost (h) considerando as probabilidades pertinentes
# de que toque cada unha das parcelas e a porcentaxe de beneficio extra (i):
ganancia_estimada = function(p,d,b,h,i){
  (p * com * 0.5 +
     p * rar * 0.3 +
     p * epi * 0.15 + 
     p * leg * 0.05) * 60 * 60 * d * (1+i/100) * (b * h + (24-h))
}

# Exemplo: queremos estimar a ganancia co número máximo de parcelas posibles dentro
# do primero rango, que son 70 (ver foto en enlace): 
enlace = "https://atlasreality.helpshift.com/hc/es/3-atlas-earth/faq/39-why-do-ad-boosts-change-and-how-do-i-see-my-current-boost-rate/"
# Para este caso queremos ver a ganancia de 70 parcelas en 1 día, considerando
# que está aplicado o 'boost' 22 de 24h. Sexan entón p = 70 e d = 1:
ganancia_estimada(70,1,20,22,10)

# Como xa fixemos anteriormente, poderiamos querer calcular canto se podería ganar
# en 1 ano natural con este número de parcelas (p = 70), considerando os eventos
# de SuperRentBoost x50 (as 24h do día) e o 'boost' normal de x20 (22 de 24h do día):
ganancia_estimada(70,333,20,22,10) + ganancia_estimada(70,32,50,24,10)


# Agora queremos escribir unha función que sexa capaz de estimar a ganancia segundo
# o número de AB que creemos que podemos ganar cada día, ou contabilizar cada cando
# realmente somos capaces de comprar unha parcela. Definamos cada un dos conceptos
# que usaremos:

# ganancia actual propia con 641 parcelas e estimada (Diego)
ganancia(300,204,97,40,32,50,24,15)+ganancia(300,204,97,40,333,2,22,15)
ganancia_estimada(641,32,50,24,15)+ganancia_estimada(641,333,2,22,15)

# ganancia actual propia con 643 parcelas e estimada (Suso)
ganancia(311,201,96,35,32,50,24,15)+ganancia(311,201,96,35,333,2,24,15)
ganancia_estimada(643,32,50,24,15)+ganancia_estimada(643,333,2,24,15)


