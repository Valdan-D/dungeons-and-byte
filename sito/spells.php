<?php
/**
 * Elenco degli incantesimi per il generatore di carte (carte.html).
 * Legge il database D&B con il ruolo di SOLA LETTURA (vede solo la vista entita_pubbliche, cioe' i libri pubblici).
 * Richiede l'accesso di un gruppo quando la protezione e' attiva.
 */
require __DIR__ . '/auth.php';
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: private, max-age=120');
if (!dnb_authenticated()) { http_response_code(401); echo json_encode(['error' => 'auth_required']); exit; }

$db = @include '/var/www/dnb_db.php';
if (!is_array($db)) { http_response_code(503); echo json_encode(['error' => 'database non configurato']); exit; }

function cap(string $s): string { $s = trim($s); return $s === '' ? '' : mb_strtoupper(mb_substr($s, 0, 1)) . mb_substr(mb_strtolower($s), 1); }

try {
    $pdo = new PDO("pgsql:host={$db['host']};port={$db['port']};dbname={$db['dbname']}", $db['user'], $db['password'],
                   [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION, PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC, PDO::ATTR_TIMEOUT => 5]);
    $righe = $pdo->query("SELECT id, nome, dati, licenza, libro, libro_titolo FROM entita_pubbliche WHERE tipologia = 'incantesimo' ORDER BY nome_norm")->fetchAll();
} catch (Throwable $e) {
    http_response_code(503); echo json_encode(['error' => 'database non raggiungibile']); exit;
}

$out = [];
foreach ($righe as $r) {
    $d = json_decode($r['dati'], true) ?: [];
    $tempo = trim((string)($d['tempo_di_lancio'] ?? '')); $durata = trim((string)($d['durata'] ?? ''));
    if (preg_match('/v(\d+(?:\.\d+)+)/i', $r['libro_titolo'], $m) && stripos($r['libro'], 'srd') !== false && $r['licenza'] === 'CC-BY-4.0') $fonte = "SRD {$m[1]} · CC BY 4.0";
    else $fonte = $r['libro_titolo'];
    $classi = array_values(array_filter(array_map(fn($c) => cap($c), explode(',', (string)($d['classi'] ?? '')))));
    $out[] = [
        'id' => (int)$r['id'], 'nome' => $r['nome'], 'livello' => is_numeric($d['livello'] ?? null) ? (int)$d['livello'] : null,
        'scuola' => cap((string)($d['scuola'] ?? '')), 'classi' => $classi,
        'tempo' => $tempo, 'gittata' => trim((string)($d['gittata'] ?? '')), 'area' => trim((string)($d['area'] ?? '')),
        'componenti' => trim((string)($d['componenti'] ?? '')), 'durata' => $durata,
        'rituale' => stripos($tempo, 'rituale') !== false || stripos($durata, 'rituale') !== false,
        'concentrazione' => stripos($durata, 'concentrazione') !== false,
        'descrizione' => (string)($d['descrizione'] ?? ''), 'avvisi' => $d['_avvisi'] ?? [], 'fonte' => $fonte,
    ];
}
echo json_encode(['incantesimi' => $out], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
