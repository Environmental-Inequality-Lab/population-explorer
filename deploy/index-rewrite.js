// CloudFront Function, viewer-request. Attach to the default behaviour.
//
// Every page lives at a directory URL -- /county/51003/ -- because those are
// the URLs people cite. S3 has no notion of a directory index when it is used
// as a REST origin behind Origin Access Control, and CloudFront's
// "Default Root Object" applies only to the root. Without this, every page on
// the site 404s and only / works.
//
// Using S3's own website-hosting endpoint would give directory indexes, but it
// requires the bucket to be publicly readable and cannot be fronted by OAC, so
// this is the supported path.

function handler(event) {
  var request = event.request;
  var uri = request.uri;

  // /county/51003/  ->  /county/51003/index.html
  if (uri.endsWith('/')) {
    request.uri = uri + 'index.html';
    return request;
  }

  // /county/51003  ->  redirect to /county/51003/
  // A permanent redirect rather than a silent rewrite, so there is one
  // canonical form of every URL and links people copy carry the slash.
  // Anything with a file extension is left alone: that is an asset.
  if (uri.indexOf('.') === -1) {
    return {
      statusCode: 301,
      statusDescription: 'Moved Permanently',
      headers: { location: { value: uri + '/' } }
    };
  }

  return request;
}
