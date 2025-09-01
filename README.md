# 💪 Workout Tracker - Streamlit Dashboard

A comprehensive workout tracking application built with Streamlit that supports strength training, cardio exercises, and detailed analytics with automatic GitHub backup.

## 🌟 Features

- **🔐 Secure Authentication**: Mandatory login system using streamlit-authenticator
- **🏋️ Strength Training**: Track sets, reps, weight, RPE, and pain levels
- **🏃 Cardio Tracking**: Log duration, distance, RPE for various cardio activities
- **📈 Analytics Dashboard**: Weekly volume analysis for muscle groups and cardio
- **☁️ GitHub Integration**: Automatic backup to GitHub (no local storage)
- **📊 Exercise Database**: Excel-based exercise library with muscle group mapping

## 🚀 Quick Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Authentication

Edit `auth.yaml` with your credentials:

```yaml
credentials:
  usernames:
    your_username:
      email: your_email@example.com
      name: Your Name
      password: YourPassword123  # Will be auto-hashed
cookie:
  name: workout_auth
  key: your_secure_random_key_12345
  expiry_days: 30
```

### 3. Setup Exercise Database

Ensure `data/exercises.xlsx` exists with these columns:
- `exercise`: Exercise name (e.g., "Bench Press")
- `primary_muscle`: Primary muscle group (e.g., "Chest")
- `secondary_muscle`: Secondary muscle group (e.g., "Triceps")

### 4. Configure GitHub Integration

Set these environment variables:

```bash
export GITHUB_TOKEN=your_github_personal_access_token
export GITHUB_REPO=username/repository_name
export GITHUB_BRANCH=main
export GITHUB_FILEPATH_STRENGTH=data/workouts.csv
export GITHUB_FILEPATH_CARDIO=data/cardio.csv
```

Or use the helper script:
```bash
python setup_github.py
```

### 5. Run the Application

```bash
streamlit run Dashboard.py
```

## 📋 Usage

### Strength Training
1. Select exercise from your database
2. Enter weight, reps, RPE, and pain levels
3. Configure sets, supersets, or dropsets
4. Save to GitHub automatically

### Cardio Tracking
1. Choose activity type or add custom
2. Enter duration and/or distance
3. Rate RPE and pain levels
4. Save to GitHub automatically

### Analytics
- View weekly volume by muscle group
- Track cardio metrics over time
- Analyze trends and progress
- Compare primary vs secondary muscle contribution

## 🔧 Configuration

### GitHub Setup Requirements

1. **Personal Access Token**: Create a GitHub PAT with repo access
2. **Repository**: Must exist and be accessible
3. **Branch**: Target branch for data storage
4. **File Paths**: CSV file locations in the repository

### Exercise Database Format

Excel file (`data/exercises.xlsx`) with columns:
- `exercise`: Unique exercise name
- `primary_muscle`: Main muscle group worked
- `secondary_muscle`: Secondary muscle group (optional)

Example:
| exercise | primary_muscle | secondary_muscle |
|----------|----------------|------------------|
| Bench Press | Chest | Triceps |
| Back Squat | Quads | Glutes |
| Deadlift | Hamstrings | Glutes |

## 🛠️ Troubleshooting

### Common Issues

1. **"No exercises found"**: Ensure `data/exercises.xlsx` exists and is properly formatted
2. **Authentication errors**: Check `auth.yaml` format and credentials
3. **GitHub save failures**: Verify environment variables and token permissions
4. **Date column errors**: Fixed in latest version - dates are properly handled

### Environment Variables

If GitHub integration isn't working, verify these variables are set:
```bash
echo $GITHUB_TOKEN
echo $GITHUB_REPO
echo $GITHUB_BRANCH
```

## 📚 File Structure

```
workout-tracker/
├── Dashboard.py              # Main application
├── auth.yaml                 # Authentication config
├── requirements.txt          # Python dependencies
├── setup_github.py          # GitHub setup helper
├── README.md                # This file
└── data/
    └── exercises.xlsx       # Exercise database
```

## 🔄 Data Flow

1. **Input**: Log workouts through Streamlit interface
2. **Validation**: Data validated and formatted
3. **Storage**: Automatically saved to GitHub repository
4. **Analytics**: Load data from GitHub for analysis
5. **Backup**: Download individual workout CSVs

## 🎯 Analytics Features

### Strength Analytics
- **Volume Metrics**: Sets, Reps, or Tonnage (Weight × Reps)
- **Muscle Groups**: Primary muscles count 1×, secondary count 0.5×
- **Time Periods**: Weekly analysis with customizable week endings
- **Trends**: Visual charts showing progress over time

### Cardio Analytics
- **Duration Tracking**: Total minutes per activity
- **Distance Tracking**: Total kilometers per activity
- **RPE Analysis**: Average perceived exertion
- **Activity Breakdown**: Compare different cardio types

## 🔒 Security Notes

- Passwords are automatically hashed using bcrypt
- GitHub tokens should have minimal required permissions
- Authentication cookies expire based on configuration
- No sensitive data stored locally

## 🚀 Future Enhancements

- [ ] COROS watch integration
- [ ] Advanced progression tracking
- [ ] Exercise video/image support
- [ ] Mobile-responsive design improvements
- [ ] Export to other fitness platforms

## 📄 License

This project is open source. Feel free to modify and distribute.

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

---

**Happy tracking! 💪**
