import assert from 'node:assert/strict';
import { elevationDownloadEstimate } from '../src/moon-loading';

assert.equal(elevationDownloadEstimate(0, 100, 0), 'Estimating download time…');
assert.equal(elevationDownloadEstimate(10, 0, 5), 'Download time estimate unavailable.');
assert.equal(elevationDownloadEstimate(50, 100, 10), 'About 10 sec left to download.');
assert.equal(elevationDownloadEstimate(10, 100, 10), 'About 2 min left to download.');
assert.equal(elevationDownloadEstimate(100, 100, 10), 'Download complete; preparing the Moon…');

console.log('Lunar elevation download estimates passed.');
