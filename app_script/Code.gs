// Recibe las filas del agente (POST)
function doPost(e) {
  const d = JSON.parse(e.postData.contents);
  const hoja = SpreadsheetApp.getActiveSpreadsheet().getSheetByName("registro");
  hoja.appendRow([d.fecha, d.evento, d.turnos, d.canal_ofrecido, d.pregunta]);
  return ContentService.createTextOutput("ok");
}

// Responde a la visita desde el navegador (GET), solo para comprobar que está activo
function doGet() {
  return ContentService.createTextOutput("ok");
}