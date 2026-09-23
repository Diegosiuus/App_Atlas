#### ANALISE DOS MINIXOGOS DO ATLAS ####
########################################

setwd("C:/Users/suso2/OneDrive/Escritorio/Atlas")

# ============================================================
# 1. LIBRERIAS
# ============================================================
library(lubridate)
library(dplyr)


# ============================================================
# 2. CARGA Y PREPARACION DE DATOS
# ============================================================
# Dejaremos los datos crudos por si queremos revisar algo en el futuro, ya que
# manipularemos en gran medida la base de datos orginal
datos_crudos <- read.table("registro_juegos.txt", sep = "", header = TRUE)

datos <- read.table("registro_juegos.txt", sep = "", header = TRUE)

# --- Funcion: detectar si una fecha es el ultimo sabado del mes ---
es_ultimo_sabado <- function(fecha) {
  wday(fecha, week_start = 1) == 6 &   # es sabado (1=lun, 6=sab)
  month(fecha + 7) != month(fecha)      # el siguiente sabado ya es otro mes
}

# --- Tipos correctos ---
datos$Fecha   <- dmy(datos$Fecha)
datos$Juego   <- as.factor(datos$Juego)
datos$Jugador <- as.factor(datos$Jugador)

# --- Duracion real segun regla del ultimo sabado ---
datos$Duracion <- ifelse(es_ultimo_sabado(datos$Fecha), 60, 120)

# --- Variables derivadas ---
datos$PropTiempo     <- datos$TiempoJugado.min. / datos$Duracion
datos$DiaMes         <- mday(datos$Fecha)
datos$RitmoVictorias <- datos$Victorias / (datos$PropTiempo * datos$Duracion)
# RitmoVictorias = victorias por minuto jugado (propio de cada jugador y juego)

# --- Eliminar columnas redundantes y Fecha ---
datos <- datos %>% select(-TiempoJugado.min., -Duracion.min., -Fecha)

# --- Vista rapida ---
str(datos)
summary(datos)


# ============================================================
# 3. ANALISIS EXPLORATORIO (EDA)
# ============================================================

# Distribucion de posiciones por jugador
boxplot(Posicion ~ Jugador, data = datos,
        main = "Posicion por jugador", ylab = "Posicion",
        col = c("steelblue", "tomato"))

# Distribucion de posiciones por juego (Diego)
boxplot(Posicion ~ Juego, datos %>% filter(Jugador %in% c("Diego")),
        main = "Posicion por juego", ylab = "Posicion",
        col = "lightgreen", las = 2)

# Distribucion de posiciones por juego (Suso)
boxplot(Posicion ~ Juego, datos %>% filter(Jugador %in% c("Suso")),
        main = "Posicion por juego", ylab = "Posicion",
        col = "lightgreen", las = 2)

# Ritmo de victorias por jugador y juego
boxplot(RitmoVictorias ~ Jugador, data = datos,
        main = "Ritmo de victorias por jugador",
        ylab = "Victorias/min", col = c("steelblue", "tomato"))

boxplot(RitmoVictorias ~ Juego, data = datos,
        main = "Ritmo de victorias por juego",
        ylab = "Victorias/min", col = "lightgreen", las = 2)

# Efecto del dia del mes sobre monedas totales
plot(datos$DiaMes, datos$MonedasTotales,
     main = "Monedas totales segun dia del mes",
     xlab = "Dia del mes", ylab = "Monedas totales", pch = 16)

# Monedas segun tipo de evento
boxplot(MonedasTotales ~ Duracion, data = datos,
        main = "Monedas totales: eventos 60min vs 120min",
        xlab = "Duracion (min)", ylab = "Monedas totales",
        col = c("gold", "steelblue"))

# Correlaciones numericas
cor(datos %>% select(where(is.numeric)), use = "complete.obs")


# ============================================================
# 4. DIVISION TRAIN / TEST
# ============================================================

set.seed(42)
n         <- nrow(datos)
idx_train <- sample(1:n, size = floor(0.8 * n))

train <- datos[idx_train, ]
test  <- datos[-idx_train, ]

cat("Filas train:", nrow(train), "| Filas test:", nrow(test), "\n")


# ============================================================
# 5. MODELOS
# ============================================================
# Flujo de prediccion encadenado:
#
#   DiaMes + Juego + Duracion
#       --> [M1] MonedasTotales estimadas
#
#   MonedasTotales + Posicion_objetivo + Juego + DiaMes
#       --> [M2] Victorias necesarias
#       (el jugador NO entra: las victorias requeridas son iguales para todos)
#
#   Juego + Jugador
#       --> [M3] RitmoVictorias (victorias/min, especifico de cada jugador y juego)
#       --> Tiempo = Victorias / Ritmo

# --- Modelo 1: estimar monedas totales del evento ---
m1 <- lm(MonedasTotales ~ Juego + DiaMes + Duracion, data = train)
summary(m1)

# --- Modelo 2: estimar victorias necesarias para una posicion ---
m2 <- lm(Victorias ~ Posicion + MonedasTotales + Juego, data = train)
summary(m2)

# --- Modelo 3: estimar ritmo de victorias por jugador y juego ---
train_m3 <- train %>% filter(!is.na(RitmoVictorias))
m3 <- lm(RitmoVictorias ~ Juego + Jugador, data = train_m3)
summary(m3)


# ============================================================
# 6. SELECCION DE MODELOS (AIC)
# ============================================================

# Alternativas para M1
m1b <- lm(MonedasTotales ~ Juego * DiaMes + Duracion, data = train)
AIC(m1, m1b)

# Alternativas para M2
m2b <- lm(Victorias ~ Posicion + MonedasTotales + Juego * DiaMes, data = train)
AIC(m2, m2b)

# Alternativas para M3 (interaccion: el ritmo puede variar por jugador segun juego)
m3b <- lm(RitmoVictorias ~ Juego * Jugador, data = train_m3)
AIC(m3, m3b)

# --- Selecciona el mejor en cada caso (diferencia > 2 puntos justifica complejidad) ---
# INSTRUCCIONES:
seleccionar_modelo <- function(m_simple, m_complejo, umbral = 2, nombre = "") {
  aic_simple  <- AIC(m_simple)
  aic_complejo <- AIC(m_complejo)
  diferencia  <- aic_simple - aic_complejo          # positivo = complejo es mejor
  
  cat(sprintf(
    "[%s] AIC simple: %.2f | AIC complejo: %.2f | Diferencia: %.2f → %s\n",
    nombre, aic_simple, aic_complejo, diferencia,
    ifelse(diferencia > umbral, "se elige COMPLEJO", "se elige SIMPLE")
  ))
  
  if (diferencia > umbral) m_complejo else m_simple
}

mejor_m1 <- seleccionar_modelo(m1, m1b, nombre = "M1")
mejor_m2 <- seleccionar_modelo(m2, m2b, nombre = "M2")
mejor_m3 <- seleccionar_modelo(m3, m3b, nombre = "M3")


# ============================================================
# 7. EVALUACION SOBRE TEST
# ============================================================

evaluar <- function(modelo, test_data, var_objetivo) {
  pred <- predict(modelo, newdata = test_data)
  real <- test_data[[var_objetivo]]
  MAE  <- mean(abs(pred - real), na.rm = TRUE)
  RMSE <- sqrt(mean((pred - real)^2, na.rm = TRUE))
  cat("MAE:", round(MAE, 2), "| RMSE:", round(RMSE, 2), "\n")
}

test_m3 <- test %>% filter(!is.na(RitmoVictorias))

cat("--- M1: MonedasTotales ---\n");  evaluar(mejor_m1, test, "MonedasTotales")
cat("--- M2: Victorias ---\n");       evaluar(mejor_m2, test, "Victorias")
cat("--- M3: RitmoVictorias ---\n");  evaluar(mejor_m3, test_m3, "RitmoVictorias")


# ============================================================
# 8. DIAGNOSTICO DE RESIDUOS
# ============================================================

par(mfrow = c(2, 2))
plot(mejor_m1, main = "M1: MonedasTotales")


par(mfrow = c(2, 2))
plot(mejor_m2, main = "M2: Victorias")

par(mfrow = c(2, 2))
plot(mejor_m3, main = "M3: RitmoVictorias")

par(mfrow = c(1, 1))


# ============================================================
# 9. FUNCION DE PREDICCION FINAL
# ============================================================
# Uso: predecir_evento("Diego", "Fishing", fecha = "2026-06-10", posicion_objetivo = 50)

predecir_evento <- function(jugador, juego, fecha, posicion_objetivo) {
  
  fecha        <- as.Date(fecha)
  duracion_min <- ifelse(es_ultimo_sabado(fecha), 60, 120)
  dia_mes      <- mday(fecha)
  
  # Paso 1: estimar monedas totales del evento
  d1 <- data.frame(
    Juego    = factor(juego, levels = levels(datos$Juego)),
    DiaMes   = dia_mes,
    Duracion = duracion_min
  )
  pred_m1     <- predict(mejor_m1, newdata = d1, interval = "prediction", level = 0.95)
  monedas_est <- pred_m1[, "fit"]
  monedas_lwr <- pred_m1[, "lwr"]
  monedas_upr <- pred_m1[, "upr"]
  
  # Paso 2: estimar victorias necesarias para esa posicion
  d2 <- data.frame(
    Juego          = factor(juego, levels = levels(datos$Juego)),
    Posicion       = posicion_objetivo,
    MonedasTotales = monedas_est,
    DiaMes         = dia_mes
  )
  pred_m2       <- predict(mejor_m2, newdata = d2, interval = "prediction", level = 0.95)
  victorias_est <- pred_m2[, "fit"]
  victorias_lwr <- pred_m2[, "lwr"]
  victorias_upr <- pred_m2[, "upr"]
  
  # Paso 3: estimar ritmo de victorias del jugador en ese juego
  d3 <- data.frame(
    Juego   = factor(juego,   levels = levels(datos$Juego)),
    Jugador = factor(jugador, levels = levels(datos$Jugador))
  )
  pred_m3   <- predict(mejor_m3, newdata = d3, interval = "prediction", level = 0.95)
  ritmo_est <- pred_m3[, "fit"]
  ritmo_lwr <- pred_m3[, "lwr"]
  ritmo_upr <- pred_m3[, "upr"]
  
  # Tiempo necesario: central + escenarios pesimista y optimista
  # Pesimista: mas victorias necesarias, ritmo mas lento
  # Optimista: menos victorias necesarias, ritmo mas rapido
  tiempo_est       <- round(victorias_est / ritmo_est)
  tiempo_pesimista <- round(victorias_upr / ritmo_lwr)
  tiempo_optimista <- round(victorias_lwr / ritmo_upr)
  
  # Resultado
  tipo_evento <- ifelse(duracion_min == 60, "Ultimo sabado (60 min)", "Normal (120 min)")
  cat("=============================================\n")
  cat("Jugador:              ", jugador, "\n")
  cat("Juego:                ", juego, "\n")
  cat("Fecha:                ", format(fecha), "\n")
  cat("Tipo de evento:       ", tipo_evento, "\n")
  cat("Posicion objetivo:    ", posicion_objetivo, "\n")
  cat("---------------------------------------------\n")
  cat("Monedas estimadas:    ", round(monedas_est), "\n")
  cat("  IC 95%:             [", round(monedas_lwr), "-", round(monedas_upr), "]\n")
  cat("Victorias necesarias: ", round(victorias_est), "\n")
  cat("  IC 95%:             [", round(victorias_lwr), "-", round(victorias_upr), "]\n")
  cat("Ritmo estimado:       ", round(ritmo_est, 3), "victorias/min\n")
  cat("  IC 95%:             [", round(ritmo_lwr, 3), "-", round(ritmo_upr, 3), "] victorias/min\n")
  cat("---------------------------------------------\n")
  cat("Tiempo necesario:     ", tiempo_est,       "min  (estimacion central)\n")
  cat("  Optimista:          ", tiempo_optimista, "min\n")
  cat("  Pesimista:          ", tiempo_pesimista, "min\n")
  cat("Duracion del evento:  ", duracion_min,     "min\n")
  cat("=============================================\n")
}

# --- Ejemplos de uso ---
predecir_evento("Suso", "Fishing(V)", fecha = "2026-06-27", posicion_objetivo = 500)
predecir_evento("Diego", "Fishing", fecha = "2026-06-28", posicion_objetivo = 100)
# Mismo evento: victorias iguales, tiempo distinto segun el ritmo de cada jugador

predecir_evento("Suso",  "Fishing",  fecha = "2026-07-14", posicion_objetivo = 500)
predecir_evento("Diego",  "Bowling",  fecha = "2026-09-10", posicion_objetivo = c(100,500,1500))


