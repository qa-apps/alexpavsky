# Article reader dependencies

Self-hosted browser builds, copied unchanged from their official npm packages:

- `Readability.js`: @mozilla/readability 0.6.0 (Apache-2.0).
- `purify.min.js`: DOMPurify 3.4.16 (Apache-2.0 OR MPL-2.0).

Corresponding upstream licenses are included alongside the files. The exact
package versions are locked in package-lock.json. They run in the browser; no
Node.js runtime or npm install is required on the production server.
