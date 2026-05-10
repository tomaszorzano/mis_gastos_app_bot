"""
Generador de reportes visuales en HTML con Chart.js.

Comando /graficos genera un archivo HTML standalone que se abre en navegador.
Incluye: números clave, torta categorías, barras mes a mes, evolución deudas.
"""
import json
from datetime import date, timedelta
from pathlib import Path
from collections import defaultdict

import storage
from config import DATA_FILE, MONEDA_PRINCIPAL, convertir_a_principal


def _ultimos_n_meses(n: int) -> list[str]:
    """Devuelve lista de meses en formato YYYY-MM, desde hace N meses hasta hoy."""
    hoy = date.today()
    meses = []
    for i in range(n - 1, -1, -1):
        mes = hoy.replace(day=1) - timedelta(days=i * 30)
        meses.append(mes.strftime("%Y-%m"))
    # Deduplicar por si hay overlap
    return sorted(list(set(meses)))


def _gastos_por_mes(usuario_id: int, mes: str) -> list:
    """Gastos de un mes específico."""
    inicio = f"{mes}-01"
    # Último día del mes (asumimos 31 como techo)
    fin = f"{mes}-31"
    return storage.obtener_gastos(usuario_id, desde=inicio, hasta=fin)


def _agregar_mes(gastos: list) -> dict:
    """Agregación de gastos: total + por categoría."""
    total = 0.0
    por_cat = defaultdict(float)
    for g in gastos:
        principal = convertir_a_principal(g["monto"], g.get("moneda", MONEDA_PRINCIPAL))
        total += principal
        por_cat[g["categoria"]] += principal
    return {
        "total": round(total, 2),
        "por_categoria": dict(sorted(por_cat.items(), key=lambda x: -x[1])),
    }


def generar_html_graficos(usuario_id: int) -> Path:
    """
    Genera archivo HTML con gráficos del mes y comparaciones.
    Devuelve Path del archivo generado.
    """
    perfil = storage.obtener_perfil(usuario_id)
    ingreso = perfil.get("ingreso_mensual", 0)
    presupuesto = perfil.get("presupuesto_mes_actual", 0)
    
    # Datos mes actual
    mes_actual = date.today().strftime("%Y-%m")
    gastos_mes = _gastos_por_mes(usuario_id, mes_actual)
    agg_actual = _agregar_mes(gastos_mes)
    
    # Datos últimos 6 meses para comparación
    meses = _ultimos_n_meses(6)
    series_total = []
    series_categorias = defaultdict(list)
    
    for mes in meses:
        gastos = _gastos_por_mes(usuario_id, mes)
        agg = _agregar_mes(gastos)
        series_total.append(agg["total"])
        # Para cada categoría, agregar su valor (0 si no hubo gastos)
        for cat in agg_actual["por_categoria"].keys():
            series_categorias[cat].append(agg["por_categoria"].get(cat, 0))
    
    # Ahorro real mes actual
    ahorro_real = ingreso - agg_actual["total"]
    
    # Comparación vs mes anterior
    if len(series_total) >= 2:
        anterior = series_total[-2]
        diff = agg_actual["total"] - anterior
        pct_cambio = (diff / anterior * 100) if anterior > 0 else 0
    else:
        diff = 0
        pct_cambio = 0
    
    # Deudas: evolución últimos 3 meses
    deudas_actual = perfil.get("deudas", [])
    # Para simplificar, mostramos solo saldo actual
    # (evolución real requiere que el user actualice saldo mes a mes en /editar_deuda)
    
    # Preparar datos para el HTML
    datos = {
        "fecha_generacion": date.today().isoformat(),
        "mes_actual": mes_actual,
        "total_gastado": int(agg_actual["total"]),
        "ingreso": ingreso,
        "ahorro_real": int(ahorro_real),
        "ahorro_pct": round((ahorro_real / ingreso * 100) if ingreso > 0 else 0, 1),
        "diff_vs_anterior": int(diff),
        "pct_cambio": round(pct_cambio, 1),
        "presupuesto": presupuesto,
        "categorias_labels": list(agg_actual["por_categoria"].keys()),
        "categorias_valores": [int(v) for v in agg_actual["por_categoria"].values()],
        "meses_labels": meses,
        "total_por_mes": [int(v) for v in series_total],
        "categorias_series": {
            cat: [int(v) for v in vals]
            for cat, vals in series_categorias.items()
        },
        "deudas": [
            {
                "nombre": d.get("nombre", "?"),
                "saldo": int(d.get("saldo", 0)),
                "cuota": int(d.get("cuota", 0)),
            }
            for d in deudas_actual
        ],
    }
    
    html = _generar_html(datos)
    
    # Guardar en carpeta temporal
    out_dir = DATA_FILE.parent / "reportes"
    out_dir.mkdir(exist_ok=True)
    out_file = out_dir / f"graficos_{usuario_id}_{mes_actual}.html"
    out_file.write_text(html, encoding="utf-8")
    return out_file


def _generar_html(datos: dict) -> str:
    """Genera el HTML con Chart.js embebido."""
    # Colores para categorías
    colores = [
        "#FF6384", "#36A2EB", "#FFCE56", "#4BC0C0", "#9966FF",
        "#FF9F40", "#FF6384", "#C9CBCF", "#4BC0C0", "#FF6384",
        "#36A2EB", "#FFCE56"
    ]
    
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Gráficos Financieros - {datos['mes_actual']}</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            background: #f5f5f5;
            padding: 20px;
            color: #333;
        }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        .header {{
            background: white;
            padding: 20px;
            border-radius: 12px;
            margin-bottom: 20px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }}
        .stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-top: 15px;
        }}
        .stat {{
            background: #f9f9f9;
            padding: 15px;
            border-radius: 8px;
            text-align: center;
        }}
        .stat-label {{ font-size: 14px; color: #666; margin-bottom: 5px; }}
        .stat-value {{ font-size: 24px; font-weight: bold; }}
        .stat-positive {{ color: #22c55e; }}
        .stat-negative {{ color: #ef4444; }}
        .chart-container {{
            background: white;
            padding: 20px;
            border-radius: 12px;
            margin-bottom: 20px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }}
        .chart-title {{ font-size: 18px; font-weight: bold; margin-bottom: 15px; }}
        canvas {{ max-height: 400px; }}
        table {{
            width: 100%;
            background: white;
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }}
        th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #eee; }}
        th {{ background: #f9f9f9; font-weight: 600; }}
        .footer {{ text-align: center; margin-top: 30px; color: #999; font-size: 13px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📊 Reporte Financiero</h1>
            <p style="color: #666; margin-top: 5px;">Mes: {datos['mes_actual']} • Generado: {datos['fecha_generacion']}</p>
            
            <div class="stats">
                <div class="stat">
                    <div class="stat-label">💰 Gastado</div>
                    <div class="stat-value">${datos['total_gastado']:,}</div>
                </div>
                <div class="stat">
                    <div class="stat-label">💵 Ahorro</div>
                    <div class="stat-value {'stat-positive' if datos['ahorro_real'] >= 0 else 'stat-negative'}">
                        ${datos['ahorro_real']:,}
                    </div>
                    <div style="font-size: 12px; color: #666; margin-top: 3px;">
                        {datos['ahorro_pct']}% del ingreso
                    </div>
                </div>
                <div class="stat">
                    <div class="stat-label">📈 vs Mes Anterior</div>
                    <div class="stat-value {'stat-negative' if datos['diff_vs_anterior'] > 0 else 'stat-positive'}">
                        {'+' if datos['diff_vs_anterior'] > 0 else ''}{datos['pct_cambio']:+.1f}%
                    </div>
                    <div style="font-size: 12px; color: #666; margin-top: 3px;">
                        ${datos['diff_vs_anterior']:+,}
                    </div>
                </div>
                <div class="stat">
                    <div class="stat-label">🎯 Presupuesto</div>
                    <div class="stat-value">${datos['presupuesto']:,}</div>
                    <div style="font-size: 12px; color: #666; margin-top: 3px;">
                        {'✅ OK' if datos['total_gastado'] <= datos['presupuesto'] else '⚠️ Pasado'}
                    </div>
                </div>
            </div>
        </div>

        <div class="chart-container">
            <div class="chart-title">📊 Gastos por Categoría (este mes)</div>
            <canvas id="chartTorta"></canvas>
        </div>

        <div class="chart-container">
            <div class="chart-title">📈 Total Gastado — Últimos 6 Meses</div>
            <canvas id="chartBarras"></canvas>
        </div>

        <div class="chart-container">
            <div class="chart-title">🔍 Categorías Mes a Mes</div>
            <canvas id="chartApilado"></canvas>
        </div>

        {'<div class="chart-container"><div class="chart-title">💳 Evolución de Deudas</div><table><thead><tr><th>Deuda</th><th>Saldo Actual</th><th>Cuota</th></tr></thead><tbody>' + ''.join(f"<tr><td>{d['nombre']}</td><td>${d['saldo']:,}</td><td>${d['cuota']:,}</td></tr>" for d in datos['deudas']) + '</tbody></table></div>' if datos['deudas'] else ''}

        <div class="footer">
            Generado por Expense Tracker Bot • {datos['fecha_generacion']}
        </div>
    </div>

    <script>
        const datos = {json.dumps(datos, ensure_ascii=False)};

        // Gráfico 1: Torta de categorías
        new Chart(document.getElementById('chartTorta'), {{
            type: 'pie',
            data: {{
                labels: datos.categorias_labels,
                datasets: [{{
                    data: datos.categorias_valores,
                    backgroundColor: {json.dumps(colores[:len(datos['categorias_labels'])])},
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: true,
                plugins: {{
                    legend: {{ position: 'bottom' }},
                    tooltip: {{
                        callbacks: {{
                            label: function(ctx) {{
                                const total = ctx.dataset.data.reduce((a,b) => a+b, 0);
                                const pct = ((ctx.parsed / total) * 100).toFixed(1);
                                return ctx.label + ': $' + ctx.parsed.toLocaleString() + ' (' + pct + '%)';
                            }}
                        }}
                    }}
                }}
            }}
        }});

        // Gráfico 2: Barras totales por mes
        new Chart(document.getElementById('chartBarras'), {{
            type: 'bar',
            data: {{
                labels: datos.meses_labels,
                datasets: [
                    {{
                        label: 'Gastado',
                        data: datos.total_por_mes,
                        backgroundColor: '#36A2EB',
                    }},
                    {{
                        label: 'Presupuesto',
                        data: Array(datos.meses_labels.length).fill(datos.presupuesto),
                        type: 'line',
                        borderColor: '#FF6384',
                        borderWidth: 2,
                        borderDash: [5, 5],
                        fill: false,
                        pointRadius: 0,
                    }}
                ]
            }},
            options: {{
                responsive: true,
                plugins: {{ legend: {{ position: 'bottom' }} }},
                scales: {{
                    y: {{
                        beginAtZero: true,
                        ticks: {{
                            callback: function(value) {{ return '$' + value.toLocaleString(); }}
                        }}
                    }}
                }}
            }}
        }});

        // Gráfico 3: Barras apiladas por categoría
        const datasets = [];
        const categorias = Object.keys(datos.categorias_series);
        categorias.forEach((cat, idx) => {{
            datasets.push({{
                label: cat,
                data: datos.categorias_series[cat],
                backgroundColor: {json.dumps(colores)}[idx % {len(colores)}],
            }});
        }});

        new Chart(document.getElementById('chartApilado'), {{
            type: 'bar',
            data: {{
                labels: datos.meses_labels,
                datasets: datasets
            }},
            options: {{
                responsive: true,
                plugins: {{ legend: {{ position: 'bottom' }} }},
                scales: {{
                    x: {{ stacked: true }},
                    y: {{
                        stacked: true,
                        beginAtZero: true,
                        ticks: {{
                            callback: function(value) {{ return '$' + value.toLocaleString(); }}
                        }}
                    }}
                }}
            }}
        }});
    </script>
</body>
</html>"""


if __name__ == "__main__":
    # Test local
    print("Test de generación (requiere datos reales)")
