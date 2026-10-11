# ADR 0003 · Identidad del paciente: token del portal y canje para Telegram

**Estado:** aceptada · 11/10/2026

## Contexto
El asistente opera Synapse **en nombre de un paciente**. No puede tener acceso directo a la base ni más permisos que el paciente: si el modelo fuera manipulado, el daño tiene que quedar acotado a lo que ese paciente ya puede hacer.

## Decisión
- **Portal:** el widget manda el mismo JWT con el que el paciente inició sesión. El asistente lo valida con `GET /api/auth/yo` y lo usa para cada llamada.
- **Telegram:** como pedir la contraseña en el chat está descartado, hay dos modos (`CREDENCIALES_MODO`):
  - **`intercambio`** (producción): el paciente toca "Seguir en Telegram" en el portal y recibe un enlace `t.me/<bot>?start=<código>` de un solo uso, que vence en 10 minutos. El bot vincula ese chat con el usuario. Después, el asistente canjea su **clave de servicio** por un token de corta duración del paciente con `POST /api/integraciones/asistente/token` ([ADR 0007 de Synapse](https://github.com/Fraqqqq/synapse/blob/main/docs/adr/0007-asistente-con-ia.md)).
  - **`demo`** (demo pública): todos los chats operan como Camila O'Connor, la paciente ficticia de la demo. Así cualquiera prueba el bot sin cuenta.

## Consecuencias
- ✅ Synapse sigue aplicando todas las reglas y la autorización (RN-22: un paciente solo gestiona lo suyo y lo de sus menores a cargo).
- ✅ La base del asistente guarda vínculos y conversaciones, nunca contraseñas ni tokens.
- ⚠️ La clave de servicio permite actuar como cualquier paciente: se guarda solo en variables de entorno y se compara en tiempo constante.
- ⚠️ En modo demo, todos los que prueban el bot comparten la misma paciente ficticia (igual que la demo de Synapse, que se reinicia cada madrugada).
