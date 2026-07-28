
export default {
  'unknown_error': 'An unknown error occurred',
  'network_error': 'Network error, please check your connection',
  'unauthorized': 'Unauthorized, please login again',
  'forbidden': 'Permission denied',
  'not_found': 'Resource not found',
  'server_error': 'Server error, please try again later',
  'validation_error': 'Validation failed',
  'source_unsupported_format': 'Unsupported source format: {{ext}}',
  'source_decode_failed': 'Failed to decode "{{filename}}" (tried: {{tried}})',
  'source_corrupt_file': 'Source file "{{filename}}" cannot be parsed: {{reason}}',
  'source_too_large': 'Source file "{{filename}}" is too large ({{size_mb}} MB > {{limit_mb}} MB)',
  'source_conflict': 'Source file "{{existing}}" already exists',
  // Image Capability
  'image_endpoint_mismatch_no_i2i': 'Model {{model}} only supports text-to-image (no /v1/images/edits)',
  'image_endpoint_mismatch_no_t2i': 'Model {{model}} only supports image-to-image (reference images required)',
  'image_capability_missing_i2i': '{{provider}}/{{model}} does not support image-to-image; configure a default model that supports image edits',
  'image_capability_missing_t2i': '{{provider}}/{{model}} does not support text-to-image; configure a default model that supports text-to-image',
};
