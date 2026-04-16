@config-change @media @processing @risk-low
Feature: Changing Media Processing Configuration
  Scenario: Enabling automatic vision processing for images
    Given auto_vision is currently disabled (cost savings)
    When the user enables auto_vision in the config
    Then the Blueprint Plan shows risk LOW (processing toggle)
    And uploaded images will automatically get vision descriptions
    And the cost impact is noted in the plan

  Scenario: Changing maximum file size
    When the user reduces max_file_size_mb from 50 to 10
    Then uploads larger than 10MB are rejected
    And existing artifacts are not affected

  Scenario: Restricting allowed MIME types
    When the user removes "image/*" from allowed_mime_types
    Then image uploads are rejected going forward
    And existing image artifacts remain accessible
