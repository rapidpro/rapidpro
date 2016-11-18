<?php

/**
 * @file bxslider-view.tpl.php
 * View template to display a list as a carousel.
 */
?>
<div class="<?php print $bxslider_classes; ?>">
  <?php foreach ($rows as $id => $row): ?>
    <div class="<?php print $row_classes[$id]; ?>"><?php print $row; ?></div>
  <?php endforeach; ?>
</div>
