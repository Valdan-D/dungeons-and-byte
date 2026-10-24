<?php
/**
 * Dungeons & Byte — API PHP (v2)
 * Serve i manuali digitalizzati (Markdown) come endpoint REST.
 *
 * Endpoints (GET api.php?action=...):
 *   projects                          → biblioteca con metadati e totali
 *   chapters&project=P                → indice capitoli di un volume
 *   chapter&project=P&file=F          → contenuto di un capitolo
 *   search&q=TESTO[&project=P]        → ricerca full-text (tutti i volumi o uno)
 *   random[&project=P]                → un capitolo a caso
 *   cover&project=P                   → immagine di copertina del volume
 *   image&project=P&file=F            → serve un'immagine
 *   index / entities                  → legacy (volumi con json/ per pagina)
 *   auth | login (POST) | logout      → protezione con password (vedi auth.php)
 *
 * La protezione con password e' spenta/accesa da /var/www/dnb_config.php
 * (comando: dnb-auth). Con la protezione spenta il sito e' completamente aperto.
 */

require __DIR__ . '/auth.php';

define('BASE_PATH', '/mnt/cond/');
define('SKIP_DIRS', ['pool', 'qa', 'tools', 'colab', 'colab_pronto', 'dataset_consolidato',
    'dataset_consolidato_backup_preclean_20260929', 'dataset_consolidato_pulito',
    'dataset_v2', 'estrazione', 'reali', 'sintetici', 'validazione']);

// Titoli leggibili per i volumi la cui cartella e' scritta in modo approssimativo
// ('nome cartella senza " - ..."' => 'Titolo'). Qui solo l'SRD (licenza CC); aggiungi i tuoi in locale.
const TITLE_OVERRIDES = [
    'IT_SRD_CC_v5.2.1' => 'SRD 5.2.1 — Documento di Riferimento del Sistema',
];

dnb_gate();   // autenticazione (no-op se la protezione e' spenta)

$action  = $_GET['action'] ?? 'projects';
$project = $_GET['project'] ?? null;

function jerr(int $code, string $msg): never {
    http_response_code($code);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode(['status' => 'error', 'error' => $msg], JSON_UNESCAPED_UNICODE);
    exit;
}
function jout(array $data): never {
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode($data, JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_SUBSTITUTE);
    exit;
}

/** Nome del volume valido e consentito (niente path traversal, rispetta la modalita' pubblica). */
function valid_project(?string $p): string {
    if (!$p || $p === '.' || $p === '..' || strpbrk($p, "/\\\0") !== false || in_array($p, SKIP_DIRS, true)) {
        jerr(400, 'project non valido');
    }
    if (!is_dir(BASE_PATH . $p)) jerr(404, 'Volume non trovato');
    if (!dnb_project_allowed($p)) jerr(401, 'auth_required');
    return $p;
}

/** Elenco dei volumi visibili al visitatore corrente. */
function list_projects(): array {
    $out = [];
    foreach (scandir(BASE_PATH) as $dir) {
        if ($dir[0] === '.' || in_array($dir, SKIP_DIRS, true)) continue;
        if (!is_dir(BASE_PATH . $dir) || !dnb_project_allowed($dir)) continue;
        $cap = glob(BASE_PATH . $dir . '/capitoli/*.md') ?: [];
        $jsn = glob(BASE_PATH . $dir . '/json/page_*.json') ?: [];
        if (!$cap && !$jsn) continue;
        $out[] = ['dir' => $dir, 'chapters' => $cap, 'json_count' => count($jsn)];
    }
    usort($out, fn($a, $b) => strcmp($a['dir'], $b['dir']));
    return $out;
}

/** Spezza "Titolo - Tipo - 5 - dnd - 1987" in titolo / tipo / edizione / anno. */
function parse_name(string $dir): array {
    $parts = array_map('trim', explode(' - ', $dir));
    $raw   = array_shift($parts);
    $title = TITLE_OVERRIDES[$raw] ?? $raw;
    if (!isset(TITLE_OVERRIDES[$raw])) {   // "Titolo Di Esempio" → "Titolo di Esempio"
        $title = preg_replace_callback('/(?<=\s)(Di|Del|Della|Delle|Dei|Degli|Dello|Da|Dal|Nel|Nella|Alla|Allo|Al|E)(?=\s)/u', fn($m) => mb_strtolower($m[1]), $title);
        $title = preg_replace('/\bD (?=[A-Z])/u', "d'", $title);
    }
    $year = null; $edition = null; $kindTxt = [];
    foreach ($parts as $p) {
        if (preg_match('/^(19|20)\d\d$/', $p))      $year = (int)$p;
        elseif (preg_match('/^\d(\.\d)?$/', $p))    $edition = $p;
        elseif (strtolower($p) !== 'dnd')           $kindTxt[] = $p;
    }
    // IT_SRD_CC_v5.2.1 ha l'edizione nel titolo
    if (!$edition && str_contains($dir, '5.5')) $edition = '5.5';
    $k = strtolower(implode(' ', $kindTxt) . ' ' . $raw);
    $kind = str_contains($k, 'regolament') ? 'regolamento'
          : (preg_match('/avventura|livello|modulo/', $k) ? 'avventura' : 'manuale');
    $era = ($year && $year < 2000) ? 'classico' : ($edition === '5.5' ? '2024' : '5e');
    return [
        'title' => $title, 'kind' => $kind, 'edition' => $edition, 'year' => $year, 'era' => $era,
        'subtitle' => implode(' · ', $kindTxt),
    ];
}

function natfiles(array $files): array { natsort($files); return array_values($files); }

/** Primo heading (# .. ###) letto dalle prime righe del file. */
function chapter_title(string $path, string $filename): string {
    $fh = @fopen($path, 'r');
    if ($fh) {
        $n = 0;
        while (($line = fgets($fh, 400)) !== false && $n++ < 12) {
            if (preg_match('/^#{1,3}\s+(.+)/', trim($line), $m)) { fclose($fh); return trim($m[1]); }
        }
        fclose($fh);
    }
    $base = preg_replace(['/\.md$/', '/^\d+-/'], '', $filename);
    return ucfirst(str_replace('-', ' ', $base));
}

function rewrite_images(string $content, string $project): string {
    $enc = urlencode($project);
    $content = preg_replace_callback('/!\[([^\]]*)\]\(\.\/images\/([^)]+)\)/', fn($m) =>
        '![' . $m[1] . '](api.php?action=image&project=' . $enc . '&file=' . rawurlencode($m[2]) . ')', $content);
    return preg_replace_callback('/src="\.\/images\/([^"]+)"/', fn($m) =>
        'src="api.php?action=image&project=' . $enc . '&file=' . rawurlencode($m[1]) . '"', $content);
}

// ─── SERVE IMMAGINE / COPERTINA ───────────────────────────────────
function serve_image(string $path): never {
    if (!is_file($path)) jerr(404, 'Immagine non trovata');
    $mime = match (strtolower(pathinfo($path, PATHINFO_EXTENSION))) {
        'png' => 'image/png', 'gif' => 'image/gif', 'webp' => 'image/webp',
        'jpx', 'jp2' => 'image/jp2', 'bmp' => 'image/bmp', default => 'image/jpeg',
    };
    header('Content-Type: ' . $mime);
    header('Content-Length: ' . filesize($path));
    header('Cache-Control: ' . (dnb_auth_enabled() ? 'private' : 'public') . ', max-age=86400');
    readfile($path);
    exit;
}

if ($action === 'image') {
    $p    = valid_project($project);
    $file = basename($_GET['file'] ?? '');
    if ($file === '') jerr(400, 'file obbligatorio');
    serve_image(BASE_PATH . $p . '/images/' . $file);
}

/** Ultimo EPUB del volume (creato dal flusso Windmill f/epub/crea_epub_libro), o null. */
function epub_file(string $p): ?string {
    $l = glob(BASE_PATH . $p . '/epub/*.epub') ?: [];
    if (!$l) return null;
    usort($l, fn($x, $y) => filemtime($y) <=> filemtime($x));
    return $l[0];
}

if ($action === 'epub') {
    $p = valid_project($project);
    $f = epub_file($p);
    if (!$f) jerr(404, 'EPUB non disponibile per questo volume');
    $nome = preg_replace('/[^A-Za-z0-9._-]+/', '_', basename($f));
    header('Content-Type: application/epub+zip');
    header('Content-Length: ' . filesize($f));
    header('Content-Disposition: attachment; filename="' . $nome . '"');
    header('Cache-Control: private, no-store');
    readfile($f);
    exit;
}

if ($action === 'cover') {
    $p    = valid_project($project);
    $imgs = array_slice(natfiles(glob(BASE_PATH . $p . '/images/*.{jpg,jpeg,png,webp}', GLOB_BRACE) ?: []), 0, 3);
    if (!$imgs) jerr(404, 'Nessuna copertina');
    usort($imgs, fn($a, $b) => filesize($b) <=> filesize($a));   // la copertina vera pesa piu' di un logo
    serve_image($imgs[0]);
}

// ─── BIBLIOTECA ───────────────────────────────────────────────────
if ($action === 'projects') {
    $projects = []; $tc = 0; $tb = 0; $ti = 0;
    foreach (list_projects() as $pr) {
        $dir   = $pr['dir'];
        $bytes = 0;
        foreach ($pr['chapters'] as $f) $bytes += filesize($f);
        $imgs = count(glob(BASE_PATH . $dir . '/images/*.{jpg,jpeg,png,webp}', GLOB_BRACE) ?: []);
        $cc   = count($pr['chapters']);
        $meta = parse_name($dir);
        $projects[] = [
            'name' => $dir, 'id' => substr(md5($dir), 0, 8),
            'type' => $cc > 0 ? 'markdown' : 'json',
            'chapter_count' => $cc, 'json_count' => $pr['json_count'],
            'has_index' => file_exists(BASE_PATH . $dir . '/json/index.json'),
            'words' => (int)round($bytes / 6.3), 'images' => $imgs, 'has_cover' => $imgs > 0,
            'epub_kb' => ($ef = epub_file($dir)) ? (int)round(filesize($ef) / 1024) : null,
        ] + $meta;
        $tc += $cc; $tb += $bytes; $ti += $imgs;
    }
    jout(['status' => 'success', 'projects' => $projects,
          'totals' => ['books' => count($projects), 'chapters' => $tc, 'words' => (int)round($tb / 6.3), 'images' => $ti]]);
}

// ─── CAPITOLI ─────────────────────────────────────────────────────
if ($action === 'chapters') {
    $p     = valid_project($project);
    $files = natfiles(glob(BASE_PATH . $p . '/capitoli/*.md') ?: []);
    if (!$files) jerr(404, 'Nessun capitolo trovato');
    $chapters = [];
    foreach ($files as $f) {
        $fn = basename($f);
        $chapters[] = ['file' => $fn, 'title' => chapter_title($f, $fn), 'size' => filesize($f)];
    }
    jout(['status' => 'success', 'project' => $p, 'chapters' => $chapters]);
}

if ($action === 'chapter') {
    $p    = valid_project($project);
    $file = basename($_GET['file'] ?? '');
    if ($file === '') jerr(400, 'file mancante');
    $path = BASE_PATH . $p . '/capitoli/' . $file;
    if (!is_file($path)) jerr(404, 'Capitolo non trovato');
    jout(['status' => 'success', 'content' => rewrite_images(file_get_contents($path), $p)]);
}

// ─── RICERCA FULL-TEXT ────────────────────────────────────────────
if ($action === 'search') {
    $q = trim((string)($_GET['q'] ?? ''));
    if (mb_strlen($q) < 2) jerr(400, 'Query troppo corta');
    $phrase = preg_replace('/\s+/u', ' ', mb_strtolower($q));
    $all    = array_values(array_filter(explode(' ', $phrase), fn($t) => mb_strlen($t) >= 2));
    $stop   = ['di','il','la','lo','le','un','una','del','della','dei','delle','degli','e','da','in','con','per','su','gli','al','alla','che','nel','nella'];
    $terms  = array_values(array_diff($all, $stop)) ?: $all;     // le parole vuote non contano, salvo query fatte solo di quelle
    if (!$terms) jerr(400, 'Query troppo corta');
    $terms = array_slice($terms, 0, 6);
    $targets = [];
    if ($project) { $targets[] = valid_project($project); }
    else foreach (list_projects() as $pr) $targets[] = $pr['dir'];

    $hits = []; $total = 0;
    foreach ($targets as $proj) {
        foreach (natfiles(glob(BASE_PATH . $proj . '/capitoli/*.md') ?: []) as $f) {
            $raw = file_get_contents($f);
            $low = mb_strtolower($raw);
            $score = 0; $first = null; $ok = true;
            foreach ($terms as $t) {
                $c = substr_count($low, $t);
                if ($c === 0) { $ok = false; break; }
                $score += $c;
                $pos = mb_strpos($low, $t);
                if ($first === null || $pos < $first) $first = $pos;
            }
            if (!$ok) continue;
            $fn    = basename($f);
            $title = chapter_title($f, $fn);
            $tl    = mb_strtolower($title);
            foreach ($terms as $t) if (str_contains($tl, $t)) $score += 40;
            if (count($all) > 1) {                                   // frase esatta: pesa molto di piu'
                $score += 15 * min(10, substr_count($low, $phrase));
                if (str_contains($tl, $phrase)) $score += 300;
            }
            if (mb_strlen($raw) < 250) $score = (int)($score * 0.5);   // frammenti minuscoli
            // snippet sul testo "pulito" (senza tag HTML), centrato sulla prima occorrenza
            $plain = preg_replace('/<[^>]*>/u', ' ', $raw);
            $plain = trim(preg_replace('/\s+/u', ' ', preg_replace('/[#*_`|]+|\$\s*\^\{[^}]*\}\s*\$/u', ' ', $plain)));
            $pl    = mb_strtolower($plain);
            $pp    = $first;
            foreach ($terms as $t) { $x = mb_strpos($pl, $t); if ($x !== false && ($pp === $first || $x < $pp)) $pp = $x; }
            $snip  = mb_substr($plain, max(0, $pp - 70), 190);
            if (mb_stripos($snip, $title) === 0) $snip = trim(mb_substr($snip, mb_strlen($title)));
            $hits[] = ['project' => $proj, 'file' => $fn, 'title' => $title, 'score' => $score, 'snippet' => $snip];
            $total++;
        }
    }
    usort($hits, fn($a, $b) => $b['score'] <=> $a['score']);
    $hits = array_slice($hits, 0, 80);
    foreach ($hits as &$h) {
        $h['id']   = substr(md5($h['project']), 0, 8);
        $h['book'] = parse_name($h['project'])['title'];
    }
    unset($h);
    jout(['status' => 'success', 'q' => $q, 'terms' => $terms, 'total' => $total, 'results' => $hits]);
}

// ─── CAPITOLO A CASO ──────────────────────────────────────────────
if ($action === 'random') {
    $targets = [];
    if ($project) { $targets[] = valid_project($project); }
    else foreach (list_projects() as $pr) $targets[] = $pr['dir'];
    $pool = [];
    foreach ($targets as $proj) foreach (glob(BASE_PATH . $proj . '/capitoli/*.md') ?: [] as $f)
        if (filesize($f) > 700) $pool[] = [$proj, $f];
    if (!$pool) jerr(404, 'Nessun capitolo');
    [$proj, $f] = $pool[array_rand($pool)];
    $fn = basename($f);
    jout(['status' => 'success', 'project' => $proj, 'id' => substr(md5($proj), 0, 8), 'file' => $fn, 'title' => chapter_title($f, $fn)]);
}

// ─── LEGACY: indice ed entita' da json/ per pagina ────────────────
if ($action === 'index') {
    $p = valid_project($project);
    $indexPath = BASE_PATH . $p . '/json/index.json';
    if (!file_exists($indexPath)) jerr(404, 'Indice non trovato');
    jout(['status' => 'success', 'index' => json_decode(file_get_contents($indexPath), true)]);
}

if ($action === 'entities') {
    $p = valid_project($project);
    $jsonDir   = BASE_PATH . $p . '/json/';
    $imagesDir = BASE_PATH . $p . '/images/';
    if (!is_dir($jsonDir)) jerr(404, 'Progetto non trovato');
    $entities = [];
    $types    = ['magic_items', 'spells', 'items', 'feats', 'species', 'classes'];
    $files    = glob($jsonDir . 'page_*.json') ?: [];
    usort($files, function ($a, $b) {
        preg_match('/page_(\d+)\.json/', $a, $ma); preg_match('/page_(\d+)\.json/', $b, $mb);
        return (int)$ma[1] - (int)$mb[1];
    });
    foreach ($files as $file) {
        $data = json_decode(file_get_contents($file), true);
        if (!$data) continue;
        $page = $data['page'] ?? null;
        $existingImages = [];
        if (is_dir($imagesDir)) foreach (glob($imagesDir . 'page_' . $page . '_img_*.png') ?: [] as $imgPath) $existingImages[] = basename($imgPath);
        if (!empty($data['skipped'])) {
            if ($existingImages) $entities[] = ['_type' => 'unrecognized', '_page' => $page, '_project' => $p, '_images' => $existingImages,
                'name_it' => 'Pagina scansionata — pag. ' . ($page ?? '?'), 'description_it' => null];
            continue;
        }
        foreach ($types as $type) foreach ($data[$type] ?? [] as $entity) {
            $entity['_type'] = $type; $entity['_page'] = $page; $entity['_project'] = $p; $entity['_images'] = $existingImages;
            $entities[] = $entity;
        }
        foreach ($data['unrecognized'] ?? [] as $text) {
            $entities[] = ['_type' => 'unrecognized', '_page' => $page, '_project' => $p, '_images' => $existingImages,
                'name_it' => 'Nota — pag. ' . ($page ?? '?'),
                'description_it' => is_string($text) ? $text : ($text['description_it'] ?? json_encode($text))];
        }
    }
    jout(['status' => 'success', 'project' => $p, 'total' => count($entities), 'entities' => $entities]);
}

jerr(400, 'Azione non riconosciuta');
