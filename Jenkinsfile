pipeline {
  agent any

  options {
    timestamps()
    disableConcurrentBuilds()
  }

  parameters {
    choice(name: 'PIPELINE_PROFILE', choices: ['quick', 'full'], description: 'Local offline pipeline profile')
    booleanParam(name: 'KEEP_GOING', defaultValue: false, description: 'Continue to later stages after a failure')
  }

  environment {
    PYTHON_EXE = 'app\\.venv\\Scripts\\python.exe'
  }

  stages {
    stage('Local Offline CI') {
      steps {
        bat """
          if not exist "%PYTHON_EXE%" set PYTHON_EXE=python
          %PYTHON_EXE% ci\\local_pipeline.py run --profile ${params.PIPELINE_PROFILE} --trigger jenkins ${params.KEEP_GOING ? '--keep-going' : ''}
        """
      }
    }
  }

  post {
    always {
      archiveArtifacts artifacts: 'logs/ci/**,artifacts/ci/**', allowEmptyArchive: true
    }
  }
}
