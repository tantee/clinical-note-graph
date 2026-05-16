// ---- Example Neo4j queries against the patient graph ----

// 1. Full patient picture
MATCH (p:Patient {patientId: 'HN123456'})-[:HAS_ENCOUNTER]->(e:Encounter)
OPTIONAL MATCH (e)-[:MENTIONS]->(c:Condition)
OPTIONAL MATCH (e)-[:PRESCRIBED]->(m:Medication)
OPTIONAL MATCH (e)-[:HAS_OBSERVATION]->(o:Observation)
RETURN p, e, collect(DISTINCT c) AS conditions, collect(DISTINCT m) AS meds, collect(DISTINCT o) AS obs
ORDER BY e.dateTime ASC;

// 2. Show which medications treat which conditions
MATCH (p:Patient {patientId: 'HN123456'})
MATCH (m:Medication {patientId: p.patientId})-[:TREATS]->(c:Condition)
RETURN m.name AS medication, c.value AS condition;

// 3. List all coding candidates for the patient with the condition they code
MATCH (cc:CodingCandidate {patientId: 'HN123456'})-[:CODES]->(c:Condition)
RETURN cc.system AS system, cc.code AS code, cc.display AS display,
       c.value AS condition, cc.confidence AS confidence
ORDER BY confidence DESC;

// 4. Latest observation values for a patient
MATCH (o:Observation {patientId: 'HN123456'})
RETURN o.name AS name, o.value AS value, o.unit AS unit, o.dateTime AS at
ORDER BY at DESC
LIMIT 25;

// 5. Find every document that supports a condition
MATCH (d:Document)-[r:EXTRACTED]->(c:Condition {patientId: 'HN123456'})
RETURN c.value AS condition, d.documentId AS doc, r.evidence AS evidence, r.confidence AS confidence;
