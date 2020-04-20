<?php

/**
 * @file
 * Contains the bxslider style plugin.
 */

/**
 * Style plugin to render each item in an ordered or unordered list.
 *
 * @ingroup views_style_plugins
 */
class bxslider_style_plugin extends views_plugin_style {
  function option_definition() {
    $options = parent::option_definition();
	//$options['mode']=array('default' => 'fade');
	$options['controls'] = array('default' => 1);
	$options['pager'] = array('default' => 1);
	$options['slideWidth']=array('default' => '');
	$options['minSlides']=array('default' => '3');
	$options['moveSlides']=array('default' => '1');
	$options['maxSlides']=array('default' => '2');
	$options['auto'] = array('default' => 1);
	$options['autoHover'] = array('default' => 1);
	$options['slideMargin'] = array('default' => 20);
	$options['infiniteLoop'] = array('default' => 1);

	
	
   
  
    return $options;
  }

  function options_form(&$form, &$form_state) {
    parent::options_form($form, $form_state);

    // Build the list of skins as options.
    $skins = array();
    foreach (bxslider_skins() as $key => $skin) {
      $skins[$key] = $skin['title'];
    }
    $skins[''] = t('None');

    // Number of options to provide in count-based options.
    $range = drupal_map_assoc(range(1, 10));
    $auto_range = $range;

    $form['controls'] = array(
      '#type' => 'select',
      '#title' => t('Controls'),
      '#default_value' => $this->options['controls'],
      '#options' => array(
        '1' => t('True'),
        '0' => t('False'),
        ),
     
	     );
    $form['pager'] = array(
      '#type' => 'select',
      '#title' => t('Pager'),
      '#default_value' => $this->options['pager'],
      '#options' => array(
        '1' => t('True'),
        '0' => t('False'),
        ),
    );
   
    $form['auto'] = array(
      '#type' => 'select',
      '#title' => t('auto'),
      '#default_value' => $this->options['auto'],
      '#options' => array(
        '1' => t('True'),
        '0' => t('False'),
        ),
    );
   
	$form['slideWidth'] = array(
      '#type' => 'textfield',
      '#title' => t('Slide Width'),
      '#default_value' => $this->options['slideWidth'],
     
    );
	$form['maxSlides'] = array(
      '#type' => 'select',
      '#title' => t('Max Slides '),
      '#description' => t('The number of items to scroll at a time.'),
      '#options' => $auto_range,
      '#default_value' => $this->options['maxSlides'],
    );
	 $form['minSlides'] = array(
      '#type' => 'select',
      '#title' => t('Min Slides'),
	  '#options' => $auto_range,
      '#default_value' => $this->options['minSlides'],
     
    );
	$form['moveSlides'] = array(
      '#type' => 'select',
      '#title' => t('Move Slides'),
	  '#options' => $auto_range,
      '#default_value' => $this->options['minSlides'],
     
    );
   $form['infiniteLoop'] = array(
      '#type' => 'select',
      '#title' => t('Infinite Loop'),
      '#default_value' => $this->options['infiniteLoop'],
      '#options' => array(
        '1' => t('True'),
        '0' => t('False'),
        ),
     
    );
	

	
    
  }

  function validate() {
    $errors = parent::validate();
    if ($this->display->handler->use_pager()) {
      $errors[] = t('The bxslider style cannot be used with a pager. Disable the "Use pager" option for this display.');
    }
    return $errors;
  }

}
